"""ops V5: single_use and rate_limit count over the server's mandate reference,
and every verdict says which of its inputs were established and which asserted.

Until V5 both counters read action_context.aae_ref, a value the caller sets. Live
on 2026-10-23 a single_use envelope was used three times with ALLOW (field left
out twice, a foreign value once), and a rate_limit of one per hour gave two more
ALLOWs after it was used up, each with a fresh foreign value. The tests below
send exactly those action_contexts.

Same pattern as tests/test_evaluator.py: every test runs in a rolled-back
transaction.
"""
import hashlib
import json
import os
import uuid

import asyncpg
import pytest_asyncio

from app.enforcement import evaluator
from app.enforcement.evaluator import evaluate_envelope
from app.enforcement.verdict_sign import verify_verdict

DB = dict(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")
AGENT = "did:moltrust:test_v5_agent"
RL_ONE = [{"type": "rate_limit", "value": 1, "window": "PT1H", "required": True}]


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


async def _envelope(conn, *, constraints, validity):
    raw = uuid.uuid4().hex.encode()
    return await conn.fetchval(
        "INSERT INTO aae_envelopes (aae_id, issuer_did, envelope_signature, mandate_scope, "
        "constraints, validity, scope_canonical, aae_version, taxonomy_version, raw_canonical) "
        "VALUES ($1,'did:moltrust:test_issuer','sig','{}'::jsonb,$2::jsonb,$3::jsonb,$4,'1.0','1.0',$5) "
        "RETURNING aae_ref",
        f"test:vc:v5{uuid.uuid4().hex[:10]}", json.dumps(constraints), json.dumps(validity),
        b"scope-" + raw, raw)


def _foreign_ref() -> str:
    return "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def _ctx(**kw):
    """An action_context WITHOUT aae_ref unless the test puts one in."""
    base = {"agent_did": AGENT, "vc_id": "vc1", "nonce": uuid.uuid4().hex}
    base.update(kw)
    return base


def _one(res, ctype):
    found = [e for e in res["evaluations"] if e["type"] == ctype]
    assert len(found) == 1, res["evaluations"]
    return found[0]


# --- criterion 3: the counters ignore action_context.aae_ref ---

async def test_single_use_without_ctx_ref_is_consumed_once(tx_conn):
    ref = await _envelope(tx_conn, constraints=[], validity={"single_use": True})
    assert (await evaluate_envelope(ref, _ctx(), tx_conn))["verdict"] == "ALLOW"
    second = await evaluate_envelope(ref, _ctx(), tx_conn)
    assert second["verdict"] == "DENY"
    assert _one(second, "single_use")["reason"] == "single_use already consumed"


async def test_single_use_foreign_ctx_ref_does_not_reset(tx_conn):
    ref = await _envelope(tx_conn, constraints=[], validity={"single_use": True})
    assert (await evaluate_envelope(ref, _ctx(aae_ref=ref), tx_conn))["verdict"] == "ALLOW"
    again = await evaluate_envelope(ref, _ctx(aae_ref=_foreign_ref()), tx_conn)
    assert again["verdict"] == "DENY"


async def test_rate_limit_counts_without_ctx_ref(tx_conn):
    ref = await _envelope(tx_conn, constraints=RL_ONE, validity={})
    first = await evaluate_envelope(ref, _ctx(), tx_conn)
    assert first["verdict"] == "ALLOW", first["evaluations"]
    second = await evaluate_envelope(ref, _ctx(), tx_conn)
    assert _one(second, "rate_limit")["reason"] == "rate_limit exceeded in window"


async def test_rate_limit_changing_ctx_ref_opens_no_new_window(tx_conn):
    ref = await _envelope(tx_conn, constraints=RL_ONE, validity={})
    assert (await evaluate_envelope(ref, _ctx(aae_ref=ref), tx_conn))["verdict"] == "ALLOW"
    for _ in range(2):
        res = await evaluate_envelope(ref, _ctx(aae_ref=_foreign_ref()), tx_conn)
        assert res["verdict"] == "DENY"
        assert _one(res, "rate_limit")["reason"] == "rate_limit exceeded in window"


async def test_missing_mandate_ref_is_deny(tx_conn):
    # Both handlers on their own, without the reference: fail-closed, also for
    # required=False, and class O, so no agent is penalized for it.
    su = await evaluator._eval_single_use({"single_use": True}, None, tx_conn)
    assert su["verdict"] == "DENY" and su["code"] == "mandate_ref_missing" and su["class"] == "O"
    rl = await evaluator._eval_rate_limit(
        {"type": "rate_limit", "value": 5, "window": "PT1H", "required": False},
        _ctx(aae_ref=_foreign_ref()), tx_conn, None)
    assert rl["verdict"] == "DENY" and rl["code"] == "mandate_ref_missing" and rl["class"] == "O"


# --- criterion 4: each input is marked established or asserted ---

async def test_inputs_mark_established_and_asserted(tx_conn):
    ref = await _envelope(tx_conn, constraints=[
        {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": False},
        {"type": "allowed_domains", "value": ["api.example.com"], "required": True},
        *RL_ONE], validity={"single_use": True})
    ctx = _ctx(aae_ref=_foreign_ref(), value=100, currency="USD", value_source="rail_verified",
               domain="api.example.com", timestamp="1999-01-01T00:00:00Z")
    res = await evaluate_envelope(ref, ctx, tx_conn,
                                  established_by_caller=frozenset({"action_context.agent_did",
                                                                   "action_context.vc_id"}))
    E, A = "established", "asserted"
    assert res["inputs"] == {
        "aae_ref": E,
        "action_context.aae_ref": A,
        "action_context.agent_did": E,
        "action_context.currency": A,
        "action_context.domain": A,
        "action_context.nonce": A,
        "action_context.timestamp": E,
        "action_context.value": A,
        "action_context.value_source": A,
        "action_context.vc_id": E,
    }
    assert _one(res, "max_transaction_value")["inputs"] == {
        "action_context.value": A, "action_context.currency": A, "action_context.value_source": A}
    assert _one(res, "allowed_domains")["inputs"] == {"action_context.domain": A}
    assert _one(res, "rate_limit")["inputs"] == {"aae_ref": E, "action_context.agent_did": E}
    assert _one(res, "single_use")["inputs"] == {"aae_ref": E}


async def test_inputs_default_to_asserted(tx_conn):
    # Without the caller vouching for anything, agent_did is the caller's word too,
    # and so is a field this module does not know.
    ref = await _envelope(tx_conn, constraints=[], validity={})
    res = await evaluate_envelope(ref, _ctx(frobnicate=1), tx_conn)
    assert res["inputs"]["action_context.agent_did"] == "asserted"
    assert res["inputs"]["action_context.frobnicate"] == "asserted"
    assert res["inputs"]["aae_ref"] == "established"


async def test_unknown_envelope_ref_is_asserted(tx_conn):
    res = await evaluate_envelope(_foreign_ref(), _ctx(), tx_conn)
    assert res["verdict"] == "DENY"
    assert res["inputs"]["aae_ref"] == "asserted"
    assert _one(res, "envelope")["inputs"] == {"aae_ref": "asserted"}


async def test_inputs_are_signed_and_stored(tx_conn):
    ref = await _envelope(tx_conn, constraints=RL_ONE, validity={})
    res = await evaluate_envelope(ref, _ctx(), tx_conn)
    assert verify_verdict(res["record"], res["verdict_signature"])
    row = await tx_conn.fetchrow("SELECT evaluations FROM aae_evaluations WHERE eval_id=$1", res["eval_id"])
    stored = json.loads(row["evaluations"])
    assert [e["inputs"] for e in stored if e["type"] == "rate_limit"] == [
        {"aae_ref": "established", "action_context.agent_did": "asserted"}]
    # a changed provenance breaks the signature like any other change to the record
    forged = json.loads(json.dumps(res["record"]))
    forged["evaluations"][0]["inputs"]["action_context.agent_did"] = "established"
    assert not verify_verdict(forged, res["verdict_signature"])
