"""Ops V6: a DID is authenticated only by proof of possession.

Before: /vc/aae/submit and /vc/aae/evaluate took X-MolTrust-DID as a login.
Anyone could name any DID, evaluate as it and collect violations in its
record (live 18.10.2026: without a key, B's DID header gave 200 and a
violation row for B). Now those two endpoints take an API key or a request
signed with the DID's key in force (RFC 9421), checked against the pinned
registry; the header stays self-identification on /enforce/check.

Also covered: a revoked DID is refused, the key in force is the one after the
retired ones (no hard-coded #key-1), and the evaluation record carries the
proof so a third party can check it without the agents table.
"""
import base64
import hashlib
import json
import os
import time
import uuid
from datetime import datetime

import asyncpg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.enforcement.acceptance_gate import AcceptanceError, _resolve_moltrust_ed25519
from tests.rfc9421_client import signed_headers, signed_post
from tests.test_acceptance_gate import _challenge, _sign, _vc
from tests.test_aae_evaluate_endpoint import _ctx, _make_envelope

DB = dict(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")
EVAL = "/vc/aae/evaluate"
SUBMIT = "/vc/aae/submit"


def _key():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


@pytest.fixture
async def agents():
    """Factory for committed test agents: agent(key=True, revoked=False) -> (priv, did)."""
    made = []
    conn = await asyncpg.connect(**DB)

    async def _make(*, key=True, revoked=False):
        priv, pub = _key()
        did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, agent_type, public_key_hex, revoked_at) "
            "VALUES ($1, $2, 'test', 'external', $3, $4)",
            did, f"tc-{did[-8:]}", pub if key else None,
            datetime.utcnow() if revoked else None)
        made.append(did)
        return priv, did

    _make.conn = conn
    yield _make
    for did in made:
        await conn.execute("DELETE FROM agent_key_history WHERE did = $1", did)
        await conn.execute("DELETE FROM agents WHERE did = $1", did)
    await conn.close()


async def _eval_body(did):
    aae_ref, aae_id = await _make_envelope(constraints=[], validity={})
    return {"aae_ref": aae_ref, "action_context": _ctx(did, aae_ref, aae_id)}


# --- (5) the DID header alone no longer authenticates on submit and evaluate ---

async def test_evaluate_with_did_header_only_is_401(async_client, agents):
    _priv, did = await agents()
    r = await async_client.post(EVAL, json=await _eval_body(did), headers={"X-MolTrust-DID": did})
    assert r.status_code == 401, r.text
    assert "self-identification" in r.json()["detail"]
    assert "RFC 9421" in r.json()["detail"]


async def test_submit_with_did_header_only_is_401(async_client, agents):
    priv, did = await agents()
    vc = _vc(did)
    r = await async_client.post(SUBMIT, json={
        "aae_jws": _sign(priv, did, vc=vc), "subject_challenge_jws": _challenge(priv, did, vc["id"])},
        headers={"X-MolTrust-DID": did})
    assert r.status_code == 401, r.text
    assert "self-identification" in r.json()["detail"]


async def test_enforce_check_still_takes_the_did_header(async_client, agents):
    _priv, did = await agents()
    r = await async_client.post("/enforce/check", json={"transaction": {"to": "0x" + "ab" * 20}},
                                headers={"X-MolTrust-DID": did})
    assert r.status_code == 200, r.text


# --- the signature path ---

async def test_signed_evaluate_is_accepted_and_the_record_proves_itself(async_client, agents):
    """(10): from the stored row alone, without agents, the proof checks out."""
    priv, did = await agents()
    payload = await _eval_body(did)
    body, headers = signed_post(priv, did, EVAL, payload)
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 200, r.text
    eval_id = r.json()["eval_id"]

    c = await asyncpg.connect(**DB)
    try:
        row = await c.fetchrow(
            "SELECT agent_did, action_context, request_proof FROM aae_evaluations WHERE eval_id = $1",
            eval_id)
    finally:
        await c.close()
    proof = json.loads(row["request_proof"])
    for field in ("signature", "signature_input", "keyid", "public_key_hex",
                  "signature_base", "content_digest", "request_body"):
        assert proof.get(field), field

    # A third party's check, using nothing but the row.
    assert proof["keyid"].startswith(row["agent_did"] + "#")
    sig = base64.b64decode(proof["signature"].split("=:", 1)[1].rstrip(":"))
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(proof["public_key_hex"])).verify(
        sig, proof["signature_base"].encode())
    lines = proof["signature_base"].split("\n")
    assert lines[-1] == '"@signature-params": ' + proof["signature_input"].split("=", 1)[1]
    assert f'"x-moltrust-did": {row["agent_did"]}' in lines
    assert f'"content-digest": {proof["content_digest"]}' in lines
    body_digest = base64.b64encode(hashlib.sha256(proof["request_body"].encode()).digest()).decode()
    assert proof["content_digest"] == f"sha-256=:{body_digest}:"
    sent = json.loads(proof["request_body"])["action_context"]
    stored = json.loads(row["action_context"])
    sent.pop("timestamp"), stored.pop("timestamp")  # the server sets the time
    assert sent == stored


async def test_signed_submit_is_accepted(async_client, agents):
    priv, did = await agents()
    vc = _vc(did)
    body, headers = signed_post(priv, did, SUBMIT, {
        "aae_jws": _sign(priv, did, vc=vc), "subject_challenge_jws": _challenge(priv, did, vc["id"])})
    r = await async_client.post(SUBMIT, content=body, headers=headers)
    assert r.status_code == 200, r.text


async def test_signature_for_another_did_cannot_evaluate_as_it(async_client, agents):
    """The live finding: A cannot act as B, not even with A's own valid key."""
    priv_a, did_a = await agents()
    _priv_b, did_b = await agents()
    body, headers = signed_post(priv_a, did_b, EVAL, await _eval_body(did_b), kid=f"{did_b}#key-1")
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 401, r.text
    assert "does not verify" in r.text


async def test_did_without_key_is_refused(async_client, agents):
    _priv, did = await agents(key=False)
    priv, _ = _key()
    body, headers = signed_post(priv, did, EVAL, await _eval_body(did))
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 401
    assert "no registered key" in r.text


async def test_nonce_is_single_use(async_client, agents):
    priv, did = await agents()
    nonce = uuid.uuid4().hex
    body, headers = signed_post(priv, did, EVAL, await _eval_body(did), nonce=nonce)
    assert (await async_client.post(EVAL, content=body, headers=headers)).status_code == 200
    body, headers = signed_post(priv, did, EVAL, await _eval_body(did), nonce=nonce)
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 401 and "nonce already used" in r.text


@pytest.mark.parametrize("tamper,reason", [
    ("stale", "older than"),
    ("body", "Content-Digest does not match"),
    ("components", "covered components"),
    ("alg", "alg must be"),
])
async def test_malformed_or_stale_signature_is_refused(async_client, agents, tamper, reason):
    priv, did = await agents()
    payload = await _eval_body(did)
    kw = {}
    if tamper == "stale":
        kw["created"] = int(time.time()) - 600
    if tamper == "components":
        kw["components"] = ("@method", "x-moltrust-did", "content-digest")
    if tamper == "alg":
        kw["alg"] = "hmac-sha256"
    body, headers = signed_post(priv, did, EVAL, payload, **kw)
    if tamper == "body":
        payload["action_context"]["action"] = "withdraw"
        body = json.dumps(payload).encode()
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 401, r.text
    assert reason in r.text


# --- (8) a revoked DID is refused, with the reason named ---

async def test_revoked_did_signature_is_refused(async_client, agents):
    priv, did = await agents(revoked=True)
    body, headers = signed_post(priv, did, EVAL, await _eval_body(did))
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 401, r.text
    assert "DID is revoked" in r.text


async def test_acceptance_gate_refuses_a_revoked_signer(agents):
    _priv, did = await agents(revoked=True)
    with pytest.raises(AcceptanceError, match="DID is revoked"):
        await _resolve_moltrust_ed25519(did, f"{did}#key-1", agents.conn)


# --- (9) the key in force, not a hard-coded #key-1 ---

async def _rotate(conn, did):
    """Retire the registered key as #key-1 and put a new one in force (#key-2)."""
    new_priv, new_pub = _key()
    await conn.execute(
        "INSERT INTO agent_key_history (did, key_index, public_key_hex, revoked_at, rotation_signature) "
        "SELECT did, 1, public_key_hex, now(), 'test-rotation' FROM agents WHERE did = $1", did)
    await conn.execute("UPDATE agents SET public_key_hex = $1 WHERE did = $2", new_pub, did)
    return new_priv


async def test_after_rotation_key_2_is_accepted_and_key_1_refused(async_client, agents):
    old_priv, did = await agents()
    new_priv = await _rotate(agents.conn, did)

    body, headers = signed_post(new_priv, did, EVAL, await _eval_body(did), kid=f"{did}#key-2")
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 200, r.text

    body, headers = signed_post(old_priv, did, EVAL, await _eval_body(did), kid=f"{did}#key-1")
    r = await async_client.post(EVAL, content=body, headers=headers)
    assert r.status_code == 401, r.text
    assert "retired" in r.text


async def test_acceptance_gate_follows_the_rotation(agents):
    _priv, did = await agents()
    await _rotate(agents.conn, did)
    assert len(await _resolve_moltrust_ed25519(did, f"{did}#key-2", agents.conn)) == 32
    with pytest.raises(AcceptanceError, match="retired"):
        await _resolve_moltrust_ed25519(did, f"{did}#key-1", agents.conn)
