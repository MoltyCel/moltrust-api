"""Tests — POST /vc/aae/evaluate durch die VOLLE Middleware-Kette (async TestClient).

Lehre #99-101: write-Endpoint durch die Middleware testen (scrub_secrets etc.).
Endpoint-Tests hinterlassen test-markierte append-only Rows (aae_envelopes/aae_evaluations
immutable) — Konvention wie IPR/credit_transactions.
"""
import json
import os
import uuid

import asyncpg


async def _make_envelope(*, constraints=None, validity=None, aae_id=None):
    aae_id = aae_id or f"test:vc:ev{uuid.uuid4().hex[:10]}"
    conn = await asyncpg.connect(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")
    raw = uuid.uuid4().hex.encode()
    try:
        row = await conn.fetchrow(
            "INSERT INTO aae_envelopes (aae_id, issuer_did, envelope_signature, mandate_scope, "
            "constraints, validity, scope_canonical, aae_version, taxonomy_version, raw_canonical) "
            "VALUES ($1,'did:moltrust:test_issuer','sig','{}'::jsonb,$2::jsonb,$3::jsonb,$4,'1.0','1.0',$5) "
            "RETURNING aae_ref, aae_id",
            aae_id, json.dumps(constraints or []), json.dumps(validity or {}), b"sc-" + raw, raw)
    finally:
        await conn.close()
    return row["aae_ref"], row["aae_id"]


def _ctx(agent_did, aae_ref, vc_id, **kw):
    base = {"aae_ref": aae_ref, "vc_id": vc_id, "agent_did": agent_did, "action": "pay",
            "nonce": uuid.uuid4().hex, "timestamp": "2026-06-01T12:00:00Z"}
    base.update(kw)
    return base


async def test_evaluate_allow_and_signature_not_scrubbed(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    aae_ref, aae_id = await _make_envelope(constraints=[], validity={})
    ctx = _ctx(did, aae_ref, aae_id)
    r = await async_client.post("/vc/aae/evaluate", json={"aae_ref": aae_ref, "action_context": ctx},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verdict"] == "ALLOW"
    assert d["eval_id"] and d["verdict_kid"] == "moltrust-registry-2026-v1"
    # verdict_signature muss vollstaendig durch scrub_secrets kommen (Allowlist-Eintrag).
    assert d["verdict_signature"] and "[REDACTED]" not in d["verdict_signature"]


async def test_evaluate_deny_self_asserted(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    aae_ref, aae_id = await _make_envelope(
        constraints=[{"type": "max_transaction_value", "value": 100, "currency": "USD", "required": True}], validity={})
    ctx = _ctx(did, aae_ref, aae_id, value=999, currency="USD")  # self_asserted required Betrag -> DENY
    r = await async_client.post("/vc/aae/evaluate", json={"aae_ref": aae_ref, "action_context": ctx},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "DENY"


async def test_nonce_missing_422(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    aae_ref, aae_id = await _make_envelope()
    ctx = _ctx(did, aae_ref, aae_id)
    del ctx["nonce"]
    r = await async_client.post("/vc/aae/evaluate", json={"aae_ref": aae_ref, "action_context": ctx},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 422, r.text


async def test_auth_missing_401(async_client):
    aae_ref, aae_id = await _make_envelope()
    r = await async_client.post("/vc/aae/evaluate",
                                json={"aae_ref": aae_ref, "action_context": _ctx("did:moltrust:x", aae_ref, aae_id)})
    assert r.status_code == 401


async def test_vc_id_mismatch_422(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    aae_ref, aae_id = await _make_envelope()
    ctx = _ctx(did, aae_ref, "test:vc:WRONG")  # vc_id != envelope aae_id -> Substitution
    r = await async_client.post("/vc/aae/evaluate", json={"aae_ref": aae_ref, "action_context": ctx},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 422, r.text


async def test_oversized_action_context_422(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    aae_ref, aae_id = await _make_envelope()
    ctx = _ctx(did, aae_ref, aae_id, pad="x" * 9000)  # > 8192 bytes
    r = await async_client.post("/vc/aae/evaluate", json={"aae_ref": aae_ref, "action_context": ctx},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 422, r.text


async def test_agent_did_not_principal_403(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    aae_ref, aae_id = await _make_envelope()
    ctx = _ctx("did:moltrust:someoneelse00", aae_ref, aae_id)  # != auth principal
    r = await async_client.post("/vc/aae/evaluate", json={"aae_ref": aae_ref, "action_context": ctx},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 403, r.text
