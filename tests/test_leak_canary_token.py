"""Leak canary at the token endpoint (ops LC-4, docs/leak-canary.md "Token canaries").

A token canary is a UCAN from the path of POST /delegation/create; presenting it
at POST /delegation/verify writes the event LC-1 writes for a credential, with
`trigger` set to `token_presentation`. The SDK's `canary.check` presents tokens
and checks events offline.

Runs against the sandbox database with both leak canary migrations applied by
the fixture below. Signing keys are made for this process.
"""
import asyncio
import datetime
import hashlib
import json
import os
import pathlib
import sys
import types
import uuid

import httpx
import pytest
import pytest_asyncio

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python" / "src"))

MIGRATIONS = (ROOT / "migrations" / "2026-10-20_leak_canary.sql",
              ROOT / "migrations" / "2026-10-20_leak_canary_token.sql")
CAP = {"moltrust://agent/task/": {"exec/run": [{}]}}


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    # Credential issuance falls back to ~/.moltrust_did_private_key when unset.
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    monkeypatch.delenv("DID_PRIVATE_KEY_ENCRYPTED", raising=False)
    cids = iter(range(10 ** 6))
    import app.main as m
    monkeypatch.setattr(m, "publish_to_ipfs", lambda vc: f"bafytokencid{next(cids):06d}")


@pytest.fixture(autouse=True)
def _no_ip_limit():
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


@pytest_asyncio.fixture
async def canary_db(app_with_lifespan):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        for migration in MIGRATIONS:
            await conn.execute(migration.read_text())
    issuers: list[str] = []
    yield issuers
    async with db_pool.acquire() as conn:
        for did in issuers:
            await conn.execute("DELETE FROM leak_canary_holds WHERE issuer_did = $1", did)
            await conn.execute("DELETE FROM leak_canary_events WHERE issuer_did = $1", did)
            await conn.execute("DELETE FROM leak_canaries WHERE issuer_did = $1", did)


@pytest_asyncio.fixture
async def parties(canary_db, credit_test_agent):
    issuer, issuer_key = await credit_test_agent()
    counterparty, counterparty_key = await credit_test_agent()
    stranger, stranger_key = await credit_test_agent()
    canary_db.append(issuer)
    return types.SimpleNamespace(issuer=issuer, issuer_key=issuer_key,
                                 counterparty=counterparty, counterparty_key=counterparty_key,
                                 stranger=stranger, stranger_key=stranger_key)


async def _issue_token(client, key, counterparty, cap=None, **over):
    body = {"counterparty_did": counterparty, "kind": "token", "capabilities": cap or CAP,
            "ttl_seconds": 600, **over}
    r = await client.post("/canary/issue", json=body, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


async def _present_token(client, token, key):
    r = await client.post("/delegation/verify", json={"token": token}, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


async def _events(client, key, canary_id=None):
    params = {"canary_id": canary_id} if canary_id else {}
    r = await client.get("/canary/events", params=params, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


def _shape(value):
    """Structure only: keys and types, never instance values."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    if isinstance(value, str):
        return "str:did:" + value.split(":")[1] if value.startswith("did:") else "str"
    return type(value).__name__


def _ucan_parts(token):
    from app.delegation import _decode_token
    header, payload, sig, _ = _decode_token(token)
    return header, payload, sig


def _trust_list():
    from app.leak_canary import trust_list
    return trust_list()


# ----------------------------------------------------------- issuance

async def test_token_canary_and_regular_token_are_structurally_equal(async_client, parties):
    r = await async_client.post(
        "/delegation/create", headers={"X-API-Key": parties.issuer_key},
        json={"delegator_did": parties.issuer, "audience_did": parties.stranger,
              "capabilities": CAP, "ttl_seconds": 600})
    assert r.status_code == 200, r.text
    regular = r.json()   # another audience, so never the same bytes as a canary
    bound = await _issue_token(async_client, parties.issuer_key, parties.counterparty)
    decoy = await _issue_token(async_client, parties.issuer_key, parties.counterparty,
                               mode="decoy")
    rh, rp, rs = _ucan_parts(regular["ucan"])
    for canary in (bound, decoy):
        delegation = canary["delegation"]
        assert _shape(delegation) == _shape(regular)
        assert delegation["issuer"] == regular["issuer"]
        assert delegation["delegator"] == regular["delegator"] == parties.issuer
        assert delegation["ucv"] == regular["ucv"]
        h, p, s = _ucan_parts(delegation["ucan"])
        assert h == rh
        assert _shape(p) == _shape(rp)
        assert p["exp"] - p["nbf"] == rp["exp"] - rp["nbf"]
        assert len(s) == len(rs)
        # The token endpoint answers both alike, field by field.
        a = await _present_token(async_client, delegation["ucan"], parties.stranger_key)
        b = await _present_token(async_client, regular["ucan"], parties.stranger_key)
        assert a["valid"] is b["valid"] is True
        assert _shape(a) == _shape(b)
        assert a["presentation"] == b["presentation"]
    assert bound["delegation"]["audience"] == parties.counterparty
    assert decoy["delegation"]["audience"] != parties.counterparty


async def test_only_the_hash_of_a_token_canary_is_stored(async_client, parties):
    from app.main import db_pool
    issued = await _issue_token(async_client, parties.issuer_key, parties.counterparty)
    token = issued["delegation"]["ucan"]
    digest = hashlib.sha256(token.encode()).hexdigest()
    async with db_pool.acquire() as conn:
        row = dict(await conn.fetchrow("SELECT * FROM leak_canaries WHERE id = $1",
                                       uuid.UUID(issued["canary_id"])))
    assert row["kind"] == "token"
    assert row["token_hash"] == digest
    assert row["canary_ref"] == issued["canary_ref"] == "sha256:" + digest
    assert row["proof_value"] is None and row["credential_type"] is None
    segments = token.split(".")
    for value in row.values():
        if isinstance(value, str):
            assert token not in value and all(seg not in value for seg in segments)


async def test_issue_token_refuses_bad_input(async_client, parties, monkeypatch):
    h = {"X-API-Key": parties.issuer_key}
    for body in ({"counterparty_did": parties.counterparty, "kind": "token"},
                 {"counterparty_did": parties.counterparty, "kind": "token", "capabilities": {}},
                 {"counterparty_did": parties.counterparty, "capabilities": CAP},
                 {"counterparty_did": parties.counterparty, "kind": "secret", "capabilities": CAP},
                 {"counterparty_did": parties.issuer, "kind": "token", "capabilities": CAP}):
        r = await async_client.post("/canary/issue", json=body, headers=h)
        assert r.status_code == 422, (body, r.text)

    # The delegation policy that refuses a token from POST /delegation/create
    # refuses a token canary the same way.
    async def refuse(conn, delegator_did, n_proofs):
        return 403, "delegation not permitted for this delegator (configure first)"
    import app.delegation
    monkeypatch.setattr(app.delegation, "policy_problem", refuse)
    r = await async_client.post("/canary/issue", headers=h, json={
        "counterparty_did": parties.counterparty, "kind": "token", "capabilities": CAP})
    assert r.status_code == 403
    r = await async_client.get("/canary/list", headers=h)
    assert r.json()["total"] == 0


# ------------------------------------------------- event at the token endpoint

async def test_token_presentation_writes_the_lc1_event(async_client, parties, tmp_path, capsys):
    """The event from the token endpoint and the event from the verifier: same
    members, same signing domain, same AER construction, one offline check."""
    from app.leak_canary import EVENT_TAG, verify_event
    from app.registry_keys import get_public_key_bytes
    from moltrust_enforce import canary
    from moltrust_enforce.cli import main as moltrust_verify

    issued = await _issue_token(async_client, parties.issuer_key, parties.counterparty)
    answer = await _present_token(async_client, issued["delegation"]["ucan"], parties.stranger_key)
    assert answer["valid"] is True
    page = await _events(async_client, parties.issuer_key, issued["canary_id"])
    assert page["total"] == 1
    token_event = page["items"][0]
    assert token_event["trigger"] == "token_presentation"
    assert token_event["canary_ref"] == issued["canary_ref"]
    assert token_event["counterparty_did"] == parties.counterparty
    assert token_event["presenter_did"] == parties.stranger
    assert token_event["credential_valid"] is True
    assert token_event["attribution"] == "open"

    # An LC-1 event from the same issuer, presented at the verifier.
    r = await async_client.post("/canary/issue", json={"counterparty_did": parties.counterparty},
                                headers={"X-API-Key": parties.issuer_key})
    vc_canary = r.json()
    vc = {k: v for k, v in vc_canary["credential"].items()
          if k not in ("renew_at", "ipfs_cid", "ipfs_url")}
    r = await async_client.post("/credentials/verify", json={"credential": vc},
                                headers={"X-API-Key": parties.stranger_key})
    assert r.status_code == 200
    vc_event = (await _events(async_client, parties.issuer_key, vc_canary["canary_id"]))["items"][0]
    assert vc_event["trigger"] == "verifier_presentation"

    # Identical construction: members, structure below them, signing domain.
    assert set(token_event) == set(vc_event) == canary.EVENT_FIELDS
    differing = {k for k in token_event
                 if _shape(token_event[k]) != _shape(vc_event[k])}
    assert differing == set(), differing
    assert canary.EVENT_TAG == EVENT_TAG
    pub = get_public_key_bytes()
    for event in (token_event, vc_event):
        assert verify_event(event, pub)
        assert event["signature"]["alg"] == "Ed25519"

    # The offline verifier: the SDK check on the whole event, and moltrust-verify
    # on its AER member, without a connection to MolTrust.
    trust = _trust_list()
    (tmp_path / "trust.json").write_text(json.dumps(trust))
    for event in (token_event, vc_event):
        report = canary.check(event, trust_list=trust)
        assert report.ok, report.failures
        assert report.recomputed_verdict == "PERMIT"
        print(f"canary.check({event['trigger']}): ok={report.ok} "
              f"attribution={report.attribution} verdict={report.recomputed_verdict}")
        (tmp_path / "aer.json").write_text(json.dumps(event["aer"]))
        print(f"moltrust-verify --input aer.json ({event['trigger']}):")
        assert moltrust_verify(["--input", str(tmp_path / "aer.json"),
                                "--trust-list", str(tmp_path / "trust.json")]) == 0
    out = capsys.readouterr().out
    with capsys.disabled():
        print("\n" + out)
    assert out.count("PASS — recomputed verdict PERMIT") == 2

    # One changed member breaks the event signature; a turned trigger as well.
    for forged in ({**token_event, "counterparty_did": parties.stranger},
                   {**token_event, "trigger": "verifier_presentation"}):
        assert not verify_event(forged, pub)
        assert not canary.check(forged, trust_list=trust).ok


async def test_expected_token_presenters_stay_silent(async_client, parties):
    bound = await _issue_token(async_client, parties.issuer_key, parties.counterparty)
    token = bound["delegation"]["ucan"]
    await _present_token(async_client, token, parties.issuer_key)
    await _present_token(async_client, token, parties.counterparty_key)
    assert (await _events(async_client, parties.issuer_key, bound["canary_id"]))["total"] == 0
    for _ in range(3):
        await _present_token(async_client, token, parties.stranger_key)
    assert (await _events(async_client, parties.issuer_key, bound["canary_id"]))["total"] == 1

    decoy = await _issue_token(async_client, parties.issuer_key, parties.counterparty,
                               mode="decoy", on_leak="none")
    await _present_token(async_client, decoy["delegation"]["ucan"], parties.issuer_key)
    assert (await _events(async_client, parties.issuer_key, decoy["canary_id"]))["total"] == 0
    await _present_token(async_client, decoy["delegation"]["ucan"], parties.counterparty_key)
    assert (await _events(async_client, parties.issuer_key, decoy["canary_id"]))["total"] == 1

    holds = (await async_client.get("/canary/holds",
                                    headers={"X-API-Key": parties.issuer_key})).json()["holds"]
    assert [x["counterparty_did"] for x in holds] == [parties.counterparty]
    listed = (await async_client.get("/canary/list",
                                     headers={"X-API-Key": parties.issuer_key})).json()
    assert {i["kind"] for i in listed["items"]} == {"token"}


async def test_regular_token_writes_no_event(async_client, parties):
    from app.main import db_pool
    r = await async_client.post(
        "/delegation/create", headers={"X-API-Key": parties.issuer_key},
        json={"delegator_did": parties.issuer, "audience_did": parties.counterparty,
              "capabilities": CAP, "ttl_seconds": 600})
    await _present_token(async_client, r.json()["ucan"], parties.stranger_key)
    await _present_token(async_client, "not.a.token", parties.stranger_key)
    async with db_pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM leak_canary_events WHERE issuer_did = $1",
                                parties.issuer)
    assert n == 0


async def test_holder_proof_by_the_counterparty_attributes_a_token_leak(async_client, parties):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from app.main import db_pool
    from moltrust_enforce import canary
    from tests.rfc9421_client import signed_post

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw).hex()
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE agents SET public_key_hex = $1 WHERE did = $2",
                           pub, parties.counterparty)
    issued = await _issue_token(async_client, parties.issuer_key, parties.counterparty)
    body, headers = signed_post(priv, parties.counterparty, "/delegation/verify",
                                {"token": issued["delegation"]["ucan"]})
    headers["X-API-Key"] = parties.stranger_key
    r = await async_client.post("/delegation/verify", content=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["presentation"] == {
        "api_key_did": parties.stranger,
        "holder_proof": {"scheme": "rfc9421", "did": parties.counterparty, "valid": True,
                         "reason": None, "subject_match": True}}

    event = (await _events(async_client, parties.issuer_key, issued["canary_id"]))["items"][0]
    assert event["attribution"] == "counterparty"
    report = canary.check(event, trust_list=_trust_list())
    assert report.ok, report.failures
    # The holder proof names the token endpoint; read as a verifier event it fails.
    as_verifier = dict(event["presentation"]["holder_proof"])
    assert not canary._holder_proof_holds(as_verifier, "/credentials/verify")


# ------------------------------------------------------- SDK canary.check

class _Bridge(httpx.BaseTransport):
    """Carries the SDK's synchronous request into the app on the test's loop."""

    def __init__(self, client, loop):
        self.client, self.loop = client, loop

    def handle_request(self, request):
        fut = asyncio.run_coroutine_threadsafe(self.client.request(
            request.method, request.url.path, content=request.read(),
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"}),
            self.loop)
        r = fut.result(60)
        return httpx.Response(r.status_code, content=r.content,
                              headers={"content-type": r.headers.get("content-type", "")})


async def test_sdk_check_presents_a_token_at_the_token_endpoint(async_client, parties):
    from moltrust_enforce import canary
    issued = await _issue_token(async_client, parties.issuer_key, parties.counterparty)
    bridge = _Bridge(async_client, asyncio.get_running_loop())
    result = await asyncio.to_thread(canary.check, issued["delegation"]["ucan"],
                                     base_url="http://test", api_key=parties.stranger_key,
                                     transport=bridge)
    assert result.valid is True and result.from_server is True
    assert result.presentation == {"api_key_did": parties.stranger, "holder_proof": None}
    bad = await asyncio.to_thread(canary.check, "not.a.token", base_url="http://test",
                                  api_key=parties.stranger_key, transport=bridge)
    assert bad.valid is False and bad.from_server is True
    page = await _events(async_client, parties.issuer_key, issued["canary_id"])
    assert page["total"] == 1
    assert canary.check(page["items"][0], trust_list=_trust_list()).ok


# ------------------------------------------------- no database: construction

def test_build_event_differs_only_in_trigger(monkeypatch):
    import app.leak_canary as lc
    from app.registry_keys import get_public_key_bytes
    from moltrust_enforce import canary
    fixed = uuid.UUID("00000000-0000-4000-8000-000000000001")
    monkeypatch.setattr(lc.uuid, "uuid4", lambda: fixed)
    now = datetime.datetime(2026, 10, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)
    row = {"id": uuid.uuid4(), "issuer_did": "did:moltrust:" + "a" * 16,
           "counterparty_did": "did:moltrust:" + "b" * 16, "mode": "bound",
           "canary_ref": "sha256:" + "c" * 64}
    presentation = {"api_key_did": None, "holder_proof": None}
    verifier = lc.build_event(row, presentation, True, now)
    token = lc.build_event(row, presentation, True, now, lc.TRIGGER_TOKEN)
    assert verifier["trigger"] == lc.TRIGGER_VERIFIER and token["trigger"] == lc.TRIGGER_TOKEN
    differing = {k for k in verifier if verifier[k] != token[k]}
    # The AER bundle carries signatures and a timestamp-free commit over the
    # same inputs, so it comes out identical; only trigger and signature differ.
    assert differing == {"trigger", "signature"}, differing
    pub = get_public_key_bytes()
    assert lc.verify_event(verifier, pub) and lc.verify_event(token, pub)
    assert canary.event_signing_input(token) == lc._event_signing_input(token)
    trust = lc.trust_list()
    assert canary.check(token, trust_list=trust).ok
    assert canary.check(verifier, trust_list=trust).ok
    with pytest.raises(ValueError):
        lc.build_event(row, presentation, True, now, "somewhere_else")


def test_token_hash_and_audience():
    import app.leak_canary as lc
    from app import delegation
    tok = delegation.mint_ucan(delegator_did="did:moltrust:" + "1" * 16,
                               audience_did="did:moltrust:" + "2" * 16, capabilities=CAP)
    assert lc.token_ref(tok) == "sha256:" + hashlib.sha256(tok.encode()).hexdigest()
    assert lc.token_audience(tok) == "did:moltrust:" + "2" * 16
    assert lc.token_audience("not.a.token") is None
    assert lc.token_audience(None) is None
