"""Leak canary (ops LC-1, ADR-LC1): issue, detect, sign, hold, MCP tools.

Runs against the sandbox database with migrations/2026-10-20_leak_canary.sql
applied by the fixture below. Signing keys are made for this process, so no
output here carries a signature by a real MolTrust key.
"""
import base64
import datetime
import json
import os
import pathlib
import re
import sys
import types
import uuid

import httpx
import pytest
import pytest_asyncio

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT / "sdk" / "python" / "src"))

MIGRATION = ROOT / "migrations" / "2026-10-20_leak_canary.sql"


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    # The credential key falls back to ~/.moltrust_did_private_key when this is
    # unset; on the server that file is the real issuer key.
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    monkeypatch.delenv("DID_PRIVATE_KEY_ENCRYPTED", raising=False)
    cids = iter(range(10 ** 6))
    import app.main as m
    monkeypatch.setattr(m, "publish_to_ipfs", lambda vc: f"bafytestcid{next(cids):06d}")


@pytest_asyncio.fixture
async def canary_db(app_with_lifespan):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute(MIGRATION.read_text())
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


async def _issue(client, key, counterparty, **over):
    body = {"counterparty_did": counterparty, **over}
    r = await client.post("/canary/issue", json=body, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


# Fields the issue response carries next to the credential, outside its proof.
# A holder that presents the response unchanged gets valid=false for any
# credential, canary or not; a holder that works strips them first.
_UNSIGNED = ("renew_at", "ipfs_cid", "ipfs_url")


def _signed(vc):
    return {k: v for k, v in vc.items() if k not in _UNSIGNED}


async def _present(client, vc, key=None):
    headers = {"X-API-Key": key} if key else {}
    r = await client.post("/credentials/verify", json={"credential": _signed(vc)}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _events(client, key, canary_id=None):
    params = {"canary_id": canary_id} if canary_id else {}
    r = await client.get("/canary/events", params=params, headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------- criterion 7

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
_DID_MOLTRUST = re.compile(r"^did:moltrust:[a-f0-9]{16}$")


def _shape(value, path=""):
    """Structure only: keys, types and string formats, never instance values."""
    if isinstance(value, dict):
        return {k: _shape(v, f"{path}.{k}") for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v, path) for v in value]
    if isinstance(value, str):
        if _TS.match(value):
            return "str:timestamp"
        if _DID_MOLTRUST.match(value):
            return "str:did:moltrust"
        if value.startswith("did:"):
            return "str:did:" + value.split(":")[1]
        return f"str:{len(value) // 16}"   # length class, not the value
    return type(value).__name__


def _constants(vc):
    """Fields every credential from this issuer carries with the same value."""
    proofs = vc["proof"] if isinstance(vc["proof"], list) else [vc["proof"]]
    return {"@context": vc["@context"], "type": vc["type"], "issuer": vc["issuer"],
            "proofs": [{k: p.get(k) for k in ("type", "cryptosuite", "verificationMethod",
                                               "proofPurpose")} for p in proofs]}


def _validity(vc):
    f = datetime.datetime.fromisoformat(vc["validFrom"].replace("Z", ""))
    u = datetime.datetime.fromisoformat(vc["validUntil"].replace("Z", ""))
    return u - f


async def _db_row_shape(pool, vc):
    from app.crypto.proof_utils import get_primary_proof_value
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM credentials WHERE proof_value = $1",
                                  get_primary_proof_value(vc))
    assert row is not None
    return {k: (v is not None) for k, v in dict(row).items() if k not in ("id",)}


async def test_canary_and_regular_credential_are_structurally_equal(async_client, parties):
    from app.main import db_pool
    r = await async_client.post("/credentials/issue", json={"subject_did": parties.counterparty},
                                headers={"X-API-Key": parties.issuer_key})
    assert r.status_code == 200, r.text
    regular = r.json()
    bound = (await _issue(async_client, parties.issuer_key, parties.counterparty))["credential"]
    decoy = (await _issue(async_client, parties.issuer_key, parties.counterparty,
                          mode="decoy"))["credential"]

    for canary in (bound, decoy):
        assert _shape(canary) == _shape(regular)
        assert _constants(canary) == _constants(regular)
        assert _validity(canary) == _validity(regular)
        assert await _db_row_shape(db_pool, canary) == await _db_row_shape(db_pool, regular)
        # The verifier answers both alike, field by field.
        a, b = await _present(async_client, canary), await _present(async_client, regular)
        assert a["valid"] is b["valid"] is True
        assert _shape(a) == _shape(b)


# ---------------------------------------------------- criteria 3, 4: event

async def test_stranger_presentation_writes_a_signed_event_with_aer(async_client, parties,
                                                                    tmp_path):
    from app.leak_canary import trust_list, verify_event
    from app.registry_keys import get_public_key_bytes
    from moltrust_enforce import PERMIT, verify_record
    from moltrust_enforce.cli import main as moltrust_verify

    issued = await _issue(async_client, parties.issuer_key, parties.counterparty)
    answer = await _present(async_client, issued["credential"], parties.stranger_key)
    assert answer["valid"] is True

    page = await _events(async_client, parties.issuer_key, issued["canary_id"])
    assert page["total"] == 1 and page["next_offset"] is None
    event = page["items"][0]
    assert event["counterparty_did"] == parties.counterparty
    assert event["issuer_did"] == parties.issuer
    assert event["presenter_did"] == parties.stranger
    assert event["canary_ref"] == issued["canary_ref"]
    assert event["trigger"] == "verifier_presentation"

    pub = get_public_key_bytes()
    assert verify_event(event, pub) is True
    forged = {**event, "counterparty_did": parties.stranger}
    assert verify_event(forged, pub) is False

    aer = event["aer"]
    result = verify_record(aer["record"], aer["bundle"], aer["mandate"], aer["transaction"],
                           trust_list())
    assert result.ok, result.failures
    assert result.recomputed_verdict == PERMIT

    (tmp_path / "aer.json").write_text(json.dumps(aer))
    (tmp_path / "trust.json").write_text(json.dumps(trust_list()))
    assert moltrust_verify(["--input", str(tmp_path / "aer.json"),
                            "--trust-list", str(tmp_path / "trust.json")]) == 0

    # The same AER material with the evidence value turned fails V2.
    from moltrust_enforce import envelope_statement, make_envelope
    item = aer["bundle"]["items"][0]
    statement, _ = envelope_statement(item)
    turned = make_envelope({**statement, "value": False}, item["signatures"])
    tampered = {**aer["bundle"], "items": [turned]}
    bad = verify_record(aer["record"], tampered, aer["mandate"], aer["transaction"], trust_list())
    assert not bad.ok


async def test_unauthenticated_presentation_is_a_leak(async_client, parties):
    issued = await _issue(async_client, parties.issuer_key, parties.counterparty)
    await _present(async_client, issued["credential"])
    event = (await _events(async_client, parties.issuer_key))["items"][0]
    assert event["presenter_did"] is None


async def test_expected_presenters_stay_silent(async_client, parties):
    bound = await _issue(async_client, parties.issuer_key, parties.counterparty)
    await _present(async_client, bound["credential"], parties.issuer_key)
    await _present(async_client, bound["credential"], parties.counterparty_key)
    assert (await _events(async_client, parties.issuer_key, bound["canary_id"]))["total"] == 0

    # A decoy has no holder: the counterparty presenting it is a leak.
    decoy = await _issue(async_client, parties.issuer_key, parties.counterparty, mode="decoy")
    await _present(async_client, decoy["credential"], parties.issuer_key)
    assert (await _events(async_client, parties.issuer_key, decoy["canary_id"]))["total"] == 0
    await _present(async_client, decoy["credential"], parties.counterparty_key)
    assert (await _events(async_client, parties.issuer_key, decoy["canary_id"]))["total"] == 1


async def test_regular_credential_writes_no_event(async_client, parties):
    from app.main import db_pool
    r = await async_client.post("/credentials/issue", json={"subject_did": parties.counterparty},
                                headers={"X-API-Key": parties.issuer_key})
    await _present(async_client, r.json(), parties.stranger_key)
    async with db_pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM leak_canary_events WHERE issuer_did = $1",
                                parties.issuer)
    assert n == 0


async def test_repeated_presentation_is_one_event(async_client, parties):
    issued = await _issue(async_client, parties.issuer_key, parties.counterparty)
    for _ in range(3):
        await _present(async_client, issued["credential"], parties.stranger_key)
    assert (await _events(async_client, parties.issuer_key, issued["canary_id"]))["total"] == 1


async def test_list_counts_events_and_paginates(async_client, parties):
    first = await _issue(async_client, parties.issuer_key, parties.counterparty)
    await _issue(async_client, parties.issuer_key, parties.counterparty, mode="decoy")
    await _present(async_client, first["credential"], parties.stranger_key)
    r = await async_client.get("/canary/list", params={"limit": 1},
                               headers={"X-API-Key": parties.issuer_key})
    page = r.json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["next_offset"] == 1
    r = await async_client.get("/canary/list", params={"limit": 1, "offset": 1},
                               headers={"X-API-Key": parties.issuer_key})
    rest = r.json()
    assert rest["next_offset"] is None
    by_id = {i["canary_id"]: i for i in page["items"] + rest["items"]}
    assert by_id[first["canary_id"]]["events"] == 1


async def test_other_issuers_get_the_same_404_as_for_nothing(async_client, parties):
    issued = await _issue(async_client, parties.issuer_key, parties.counterparty)
    await _present(async_client, issued["credential"], parties.stranger_key)
    foreign = await async_client.get("/canary/events", params={"canary_id": issued["canary_id"]},
                                     headers={"X-API-Key": parties.stranger_key})
    missing = await async_client.get("/canary/events", params={"canary_id": str(uuid.uuid4())},
                                     headers={"X-API-Key": parties.stranger_key})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    mine = await async_client.get("/canary/list", headers={"X-API-Key": parties.stranger_key})
    assert mine.json()["total"] == 0
    assert (await _events(async_client, parties.stranger_key))["total"] == 0


async def test_issue_refuses_bad_input(async_client, parties):
    h = {"X-API-Key": parties.issuer_key}
    for body in ({"counterparty_did": parties.issuer},
                 {"counterparty_did": parties.counterparty, "mode": "loud"},
                 {"counterparty_did": parties.counterparty, "on_leak": "forbid"},
                 {"counterparty_did": "not-a-did"}):
        r = await async_client.post("/canary/issue", json=body, headers=h)
        assert r.status_code == 422, (body, r.text)
    assert (await async_client.post("/canary/issue",
                                    json={"counterparty_did": parties.counterparty})).status_code == 401


# ------------------------------------------------------- criterion 5: hold

def _mandate():
    from app.enforcement.enforce_check import action_digest
    return {"grants": [{
        "action_binding": action_digest({"verb": "pay"}),
        "disposition": "allow",
        "type_fields": ["verb"],
        "constraints": [{"type": "range", "field": "amount", "lo": 0, "hi": 1000}],
    }]}


def _tx(counterparty):
    return {"action": {"verb": "pay"}, "amount": 10, "counterparty_did": counterparty}


async def test_event_puts_the_counterparty_on_hold_in_the_mandate(async_client, parties):
    from app.enforcement.enforce_check import PENDING, PERMIT, enforce_check
    h = {"X-API-Key": parties.issuer_key}
    hook = {"mandate": _mandate(), "counterparty_field": "counterparty_did"}

    before = (await async_client.post("/canary/enforce-hook", json=hook, headers=h)).json()
    assert before["grants_added"] == 0
    assert enforce_check(before["mandate"], _tx(parties.counterparty))["verdict"] == PERMIT

    issued = await _issue(async_client, parties.issuer_key, parties.counterparty)
    await _present(async_client, issued["credential"], parties.stranger_key)
    holds = (await async_client.get("/canary/holds", headers=h)).json()["holds"]
    assert [x["counterparty_did"] for x in holds] == [parties.counterparty]

    after = (await async_client.post("/canary/enforce-hook", json=hook, headers=h)).json()
    assert after["held_counterparties"] == [parties.counterparty]
    held = enforce_check(after["mandate"], _tx(parties.counterparty))
    other = enforce_check(after["mandate"], _tx(parties.stranger))
    assert held["verdict"] == PENDING, held["reason"]
    assert other["verdict"] == PERMIT, other["reason"]

    r = await async_client.post("/canary/holds/release",
                                json={"counterparty_did": parties.counterparty}, headers=h)
    assert r.status_code == 200
    released = (await async_client.post("/canary/enforce-hook", json=hook, headers=h)).json()
    assert released["grants_added"] == 0
    assert enforce_check(released["mandate"], _tx(parties.counterparty))["verdict"] == PERMIT


async def test_on_leak_none_records_without_hold(async_client, parties):
    issued = await _issue(async_client, parties.issuer_key, parties.counterparty, on_leak="none")
    await _present(async_client, issued["credential"], parties.stranger_key)
    assert (await _events(async_client, parties.issuer_key))["total"] == 1
    holds = (await async_client.get("/canary/holds",
                                    headers={"X-API-Key": parties.issuer_key})).json()["holds"]
    assert holds == []


def test_apply_holds_leaves_non_allow_grants_and_respects_bounds():
    from app.enforcement.enforce_check import MAX_CONSTRAINTS_PER_GRANT
    from app.leak_canary import apply_holds
    m = _mandate()
    m["grants"].append({**m["grants"][0], "disposition": "forbid", "constraints": []})
    out = apply_holds(m, ["did:moltrust:aaaaaaaaaaaaaaaa", "did:moltrust:bbbbbbbbbbbbbbbb"],
                      "counterparty_did")
    assert [g["disposition"] for g in out["grants"]] == ["hold", "hold", "allow", "forbid"]
    assert out["grants"][0]["constraints"][-1] == {
        "type": "exact", "field": "counterparty_did", "value": "did:moltrust:aaaaaaaaaaaaaaaa"}
    assert m["grants"][0]["constraints"] == _mandate()["grants"][0]["constraints"]
    assert apply_holds(m, [], "counterparty_did") == m

    full = _mandate()
    full["grants"][0]["constraints"] = [{"type": "range", "field": "amount", "lo": 0, "hi": 1}] \
        * MAX_CONSTRAINTS_PER_GRANT
    with pytest.raises(ValueError):
        apply_holds(full, ["did:moltrust:aaaaaaaaaaaaaaaa"], "counterparty_did")


# ------------------------------------------------------- criterion 6: MCP

class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _ctx(key):
    return types.SimpleNamespace(request_context=types.SimpleNamespace(
        request=types.SimpleNamespace(headers={"x-api-key": key})))


async def test_mcp_tools_issue_list_and_events(async_client, parties, app_with_lifespan,
                                               monkeypatch):
    import canary_mcp_tools as tools_mod
    monkeypatch.setattr(tools_mod, "API_URL", "http://test")
    monkeypatch.setattr(tools_mod, "_TRANSPORT", httpx.ASGITransport(app=app_with_lifespan))
    fake = FakeMCP()
    tools_mod.register_canary_tools(fake)
    assert sorted(fake.tools) == ["canary_events", "canary_issue", "canary_list"]
    t = fake.tools

    assert json.loads(await t["canary_issue"](parties.counterparty))["error"] == "api_key_required"

    issued = json.loads(await t["canary_issue"](parties.counterparty, ctx=_ctx(parties.issuer_key)))
    assert issued["mode"] == "bound" and issued["credential"]["proof"]
    await _present(async_client, issued["credential"], parties.stranger_key)

    listed = json.loads(await t["canary_list"](ctx=_ctx(parties.issuer_key)))
    assert listed["total"] == 1 and listed["items"][0]["events"] == 1
    events = json.loads(await t["canary_events"](canary_id=issued["canary_id"],
                                                 ctx=_ctx(parties.issuer_key)))
    assert events["total"] == 1
    assert events["items"][0]["counterparty_did"] == parties.counterparty

    refused = json.loads(await t["canary_issue"](parties.counterparty, mode="loud",
                                                 ctx=_ctx(parties.issuer_key)))
    assert refused["error"] == "api_error" and refused["status"] == 422


def test_trust_list_shape():
    from app.leak_canary import SOURCE_ID, trust_list
    from moltrust_enforce import trust_list_problem
    tl = trust_list()
    assert trust_list_problem(tl) is None
    assert list(tl["sources"]) == [SOURCE_ID]
    assert len(base64.b64decode(tl["sources"][SOURCE_ID]["keys"][0]["public_key"])) == 32
