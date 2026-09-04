"""Tests — D-1 Acceptance-Gate (Phase A, did:moltrust). verify_aae_jws + /vc/aae/submit.

The gate runs §5 Step 1+2+4, so every accepting case supplies a subject-binding
challenge-response. Step 4 itself is covered in tests/test_subject_binding.py;
here it appears only as the input the gate now requires.

Unit-Tests laufen in rollback-tx (Agent + ggf. Envelope-Rows). Endpoint-Tests gehen durch
die volle Middleware (async_client); sie registrieren einen committeten Test-Agenten
(danach geloescht) und hinterlassen append-only Envelope-Rows (immutable, Konvention).
"""
import json
import os
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio
from jwt.api_jws import PyJWS
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.enforcement import acceptance_gate as ag
from app.enforcement.acceptance_gate import verify_aae_jws, AcceptanceError
from app.enforcement.subject_binding import RELYING_PARTY_AUD, issue_challenge

DB = dict(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")


def _new_did_key():
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    return priv, did, pub_hex


async def _register_agent(conn, did, pub_hex):
    await conn.execute(
        "INSERT INTO agents (did, display_name, platform, agent_type, public_key_hex) "
        "VALUES ($1, $2, 'test', 'external', $3) "
        "ON CONFLICT (did) DO UPDATE SET public_key_hex = EXCLUDED.public_key_hex",
        did, f"tc-{did[-8:]}", pub_hex)


def _vc(did, aae_id=None, mandate=None, constraints=None, validity=None):
    return {
        "id": aae_id or f"test:vc:{uuid.uuid4().hex[:12]}",
        "issuer": did,
        "credentialSubject": {"id": did, "aae": {
            "mandate": mandate if mandate is not None else {"scope": ["payments:write"]},
            "constraints": constraints if constraints is not None else
                [{"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True}],
            "validity": validity if validity is not None else
                {"not_before": "2026-01-01T00:00:00Z", "not_after": "2027-01-01T00:00:00Z"},
        }},
    }


def _sign(priv, did, *, payload=None, vc=None, alg="EdDSA", key=None, cty="aae+json", kid=None):
    if payload is None:
        payload = json.dumps(vc if vc is not None else _vc(did)).encode()
    headers = {}
    if cty is not None:
        headers["cty"] = cty
    headers["kid"] = kid if kid is not None else f"{did}#key-1"
    sk = key if key is not None else (priv if alg == "EdDSA" else "")
    return PyJWS().encode(payload, key=sk, algorithm=alg, headers=headers)


def _challenge(priv, did, aae_id, *, aud=RELYING_PARTY_AUD):
    """A valid §5 Step 4 response for `aae_id`, signed by the subject key."""
    ch = issue_challenge(aae_id, aud=aud)
    claims = {"nonce": ch["nonce"], "aud": aud,
              "iat": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "aae_id": aae_id}
    return PyJWS().encode(json.dumps(claims).encode(), key=priv, algorithm="EdDSA",
                          headers={"kid": f"{did}#key-1"})


@pytest_asyncio.fixture
async def tx_conn():
    conn = await asyncpg.connect(**DB)
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


# ---------------- verify_aae_jws unit tests ----------------

async def test_valid_jws_accepts_and_exact_bytes(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    vc = _vc(did)
    payload = json.dumps(vc).encode()
    jws = _sign(priv, did, payload=payload)
    v = await verify_aae_jws(jws, tx_conn,
                             subject_challenge_jws=_challenge(priv, did, vc["id"]))
    assert v["issuer_did"] == did and v["issuer_trust_tier"] == "trusted"
    assert v["raw_canonical"] == payload  # exakt signierte bytes, KEIN re-serialize


async def test_alg_none_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    jws = _sign(priv, did, alg="none")
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_alg_hs256_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    jws = _sign(priv, did, alg="HS256", key=b"shared-secret")
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_kid_path_traversal_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    jws = _sign(priv, did, kid="did:moltrust:../../etc#key-1")
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_wrong_kid_fragment_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    jws = _sign(priv, did, kid=f"{did}#key-2")  # nicht die assertionMethod-VM
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_signing_did_not_issuer_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    vc = _vc(did)
    vc["issuer"] = "did:moltrust:00000000feedface"  # issuer != signing DID
    jws = _sign(priv, did, vc=vc)
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_key_substitution_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    other_priv, _, _ = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)  # registriert pub von priv
    jws = _sign(other_priv, did)  # aber signiert mit FREMDEM key
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_cty_wrong_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    jws = _sign(priv, did, cty="application/json")
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_duplicate_json_keys_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    # handgebauter payload mit doppeltem Key (json.dumps wuerde das nie erzeugen)
    payload = ('{"id":"test:vc:dup","id":"evil","issuer":"' + did + '",'
               '"credentialSubject":{"id":"' + did + '","aae":{"mandate":{},"constraints":[],"validity":{}}}}').encode()
    jws = _sign(priv, did, payload=payload)
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_unregistered_signing_did_rejected(tx_conn):
    priv, did, _ = _new_did_key()  # NICHT registriert
    jws = _sign(priv, did)
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


async def test_did_web_not_implemented(tx_conn):
    priv, _, _ = _new_did_key()
    wdid = "did:web:example.com"
    vc = _vc(wdid)
    jws = _sign(priv, wdid, vc=vc, kid=f"{wdid}#key-1")
    with pytest.raises(NotImplementedError):
        await verify_aae_jws(jws, tx_conn)


async def test_oversized_payload_rejected(tx_conn):
    # payload-size-cap greift VOR verify (DoS-Schutz)
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    vc = _vc(did)
    vc["credentialSubject"]["aae"]["mandate"]["pad"] = "x" * 9000  # > 8KB payload
    jws = _sign(priv, did, vc=vc)
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn)


# ---------------- /vc/aae/submit endpoint (full middleware) ----------------

async def _commit_agent(did, pub_hex):
    c = await asyncpg.connect(**DB)
    try:
        await _register_agent(c, did, pub_hex)
    finally:
        await c.close()


async def _delete_agent(did):
    c = await asyncpg.connect(**DB)
    try:
        await c.execute("DELETE FROM agents WHERE did = $1", did)
    finally:
        await c.close()


async def test_submit_endpoint_accepts_jws(async_client):
    priv, did, pubhex = _new_did_key()
    await _commit_agent(did, pubhex)
    try:
        vc = _vc(did)
        jws = _sign(priv, did, vc=vc)
        r = await async_client.post(
            "/vc/aae/submit",
            json={"aae_jws": jws, "subject_challenge_jws": _challenge(priv, did, vc["id"])},
            headers={"X-MolTrust-DID": did})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["stored"] is True and d["issuer_trust_tier"] == "trusted"
        assert d["aae_ref"].startswith("sha256:")
    finally:
        await _delete_agent(did)


async def test_submit_auth_missing_401(async_client):
    priv, did, pubhex = _new_did_key()
    vc = _vc(did)
    jws = _sign(priv, did, vc=vc)
    r = await async_client.post("/vc/aae/submit", json={
        "aae_jws": jws, "subject_challenge_jws": _challenge(priv, did, vc["id"])})
    assert r.status_code == 401


async def test_submit_invalid_jws_422(async_client):
    priv, did, _ = _new_did_key()  # nicht registriert -> verify schlaegt fehl
    vc = _vc(did)
    jws = _sign(priv, did, vc=vc)
    r = await async_client.post(
        "/vc/aae/submit",
        json={"aae_jws": jws, "subject_challenge_jws": _challenge(priv, did, vc["id"])},
        headers={"X-MolTrust-DID": did})
    assert r.status_code == 422, r.text


async def test_submit_replay_409(async_client):
    priv, did, pubhex = _new_did_key()
    await _commit_agent(did, pubhex)
    try:
        vc = _vc(did)
        jws = _sign(priv, did, vc=vc)  # identischer JWS -> identischer aae_ref (PK)
        # Each submit needs its own challenge: a spent nonce would fail Step 4 first
        # and hide the aae_ref collision this test is about.
        r1 = await async_client.post(
            "/vc/aae/submit",
            json={"aae_jws": jws, "subject_challenge_jws": _challenge(priv, did, vc["id"])},
            headers={"X-MolTrust-DID": did})
        assert r1.status_code == 200, r1.text
        r2 = await async_client.post(
            "/vc/aae/submit",
            json={"aae_jws": jws, "subject_challenge_jws": _challenge(priv, did, vc["id"])},
            headers={"X-MolTrust-DID": did})
        assert r2.status_code == 409, r2.text  # exakter Replay durch PK geblockt
    finally:
        await _delete_agent(did)


async def test_submit_without_challenge_422(async_client):
    priv, did, pubhex = _new_did_key()
    await _commit_agent(did, pubhex)
    try:
        r = await async_client.post("/vc/aae/submit", json={"aae_jws": _sign(priv, did)},
                                    headers={"X-MolTrust-DID": did})
        assert r.status_code == 422, r.text
        assert "subject_challenge_jws" in r.text
    finally:
        await _delete_agent(did)


async def test_challenge_endpoint_issues_bound_nonce(async_client):
    priv, did, pubhex = _new_did_key()
    await _commit_agent(did, pubhex)
    try:
        r = await async_client.post("/vc/aae/challenge", json={"aae_id": "test:vc:abc"},
                                    headers={"X-MolTrust-DID": did})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["aae_id"] == "test:vc:abc" and d["aud"] == RELYING_PARTY_AUD
        assert len(bytes.fromhex(d["nonce"].split(".")[0])) == 16
    finally:
        await _delete_agent(did)


async def test_challenge_endpoint_requires_aae_id(async_client):
    priv, did, pubhex = _new_did_key()
    await _commit_agent(did, pubhex)
    try:
        r = await async_client.post("/vc/aae/challenge", json={},
                                    headers={"X-MolTrust-DID": did})
        assert r.status_code == 422, r.text
    finally:
        await _delete_agent(did)


async def test_challenge_for_another_aae_rejected(tx_conn):
    priv, did, pubhex = _new_did_key()
    await _register_agent(tx_conn, did, pubhex)
    vc = _vc(did)
    jws = _sign(priv, did, vc=vc)
    other = _challenge(priv, did, "test:vc:someone-else")
    with pytest.raises(AcceptanceError):
        await verify_aae_jws(jws, tx_conn, subject_challenge_jws=other)
