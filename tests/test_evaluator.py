"""Tests — AAE Evaluator (D3 Komponente 2, Schritt 3).

Die meisten Tests laufen in rollback-wrapped tx (aae_evaluations/aae_envelopes immutable).
EINE Ausnahme: der echte Parallel-TOCTOU-Test braucht committete Rows ueber 2 Verbindungen
-> hinterlaesst test-markierte append-only Rows (Konvention wie IPR/credit_transactions).
"""
import asyncio
import json
import os
import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.enforcement import evaluator
from app.enforcement.evaluator import evaluate_envelope
from app.enforcement.verdict_sign import verify_verdict

DB = dict(host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack")


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


async def _insert_envelope(conn, *, constraints, validity, aae_id=None):
    aae_id = aae_id or f"test:vc:{uuid.uuid4().hex[:12]}"
    raw = uuid.uuid4().hex.encode()
    row = await conn.fetchrow(
        "INSERT INTO aae_envelopes (aae_id, issuer_did, envelope_signature, mandate_scope, "
        "constraints, validity, scope_canonical, aae_version, taxonomy_version, raw_canonical) "
        "VALUES ($1,'did:moltrust:test_issuer','sig','{}'::jsonb,$2::jsonb,$3::jsonb,$4,'1.0','1.0',$5) "
        "RETURNING aae_ref",
        aae_id, json.dumps(constraints), json.dumps(validity), b"scope-" + raw, raw)
    return row["aae_ref"]


async def _insert_prior_eval(conn, agent_did, verdict="ALLOW", aae_ref="sha256:" + "a" * 64):
    await conn.execute(
        "INSERT INTO aae_evaluations (aae_ref, agent_did, action_context, evaluations, verdict, "
        "value_source, evaluator_version, nonce, verdict_signature, verdict_kid) "
        "VALUES ($1,$2,'{}'::jsonb,'[]'::jsonb,$3,'n/a','1.0',$4,'sig','kid')",
        aae_ref, agent_did, verdict, uuid.uuid4().hex)


def _ctx(aae_ref, **kw):
    base = {"aae_ref": aae_ref, "vc_id": "vc1", "agent_did": "did:moltrust:test_agent",
            "action": "pay", "timestamp": "2026-06-01T12:00:00Z", "nonce": uuid.uuid4().hex}
    base.update(kw)
    return base


# --- max_transaction_value ---
async def test_mtv_allow_rail_verified(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True}], validity={})
    res = await evaluate_envelope(ref, _ctx(ref, value=300, currency="USD", value_source="rail_verified"), tx_conn)
    assert res["verdict"] == "ALLOW", res["evaluations"]


async def test_mtv_deny_exceeds(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True}], validity={})
    res = await evaluate_envelope(ref, _ctx(ref, value=600, currency="USD", value_source="rail_verified"), tx_conn)
    assert res["verdict"] == "DENY"


async def test_mtv_self_asserted_required_denies(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True}], validity={})
    res = await evaluate_envelope(ref, _ctx(ref, value=300, currency="USD", value_source="self_asserted"), tx_conn)
    assert res["verdict"] == "DENY"  # self_asserted darf required Betrag nicht hart erfuellen


async def test_mtv_currency_mismatch_denies(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True}], validity={})
    res = await evaluate_envelope(ref, _ctx(ref, value=300, currency="EUR", value_source="rail_verified"), tx_conn)
    assert res["verdict"] == "DENY"


async def test_mtv_numeric_bypass_rejected(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True}], validity={})
    for bad in [-1000, "300", 10 ** 16, 300.5, True]:  # negativ/str/over-bound/float/bool -> reject
        res = await evaluate_envelope(ref, _ctx(ref, value=bad, currency="USD", value_source="rail_verified"), tx_conn)
        assert res["verdict"] == "DENY", f"bad value {bad!r} should be rejected"


# --- allowed_domains ---
async def test_allowed_domains_allow_and_deny(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "allowed_domains", "value": ["booking.example.com"], "required": True}], validity={})
    assert (await evaluate_envelope(ref, _ctx(ref, domain="booking.example.com"), tx_conn))["verdict"] == "ALLOW"
    assert (await evaluate_envelope(ref, _ctx(ref, domain="evil.com"), tx_conn))["verdict"] == "DENY"
    # exakter Match: Subdomain ist NICHT erlaubt (kein substring-bypass)
    assert (await evaluate_envelope(ref, _ctx(ref, domain="x.booking.example.com"), tx_conn))["verdict"] == "DENY"


# --- rate_limit (stateful, count ueber aae_evaluations) ---
async def test_rate_limit_window_count(tx_conn):
    agent = "did:moltrust:test_agent"
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "rate_limit", "value": 2, "window": "PT1H", "required": False}], validity={})
    # 1 prior fuer DIESEN envelope (per-(agent,aae_ref)-scope) -> noch unter Limit -> ALLOW
    await _insert_prior_eval(tx_conn, agent, aae_ref=ref)
    assert (await evaluate_envelope(ref, _ctx(ref), tx_conn))["verdict"] == "ALLOW"
    # jetzt 2 (prior + eben geschriebenes ALLOW) -> Limit erreicht -> DENY
    res = await evaluate_envelope(ref, _ctx(ref), tx_conn)
    assert res["verdict"] == "DENY"


# --- single_use (stateful) ---
async def test_single_use_collision(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[], validity={"single_use": True})
    assert (await evaluate_envelope(ref, _ctx(ref), tx_conn))["verdict"] == "ALLOW"  # erste Nutzung
    assert (await evaluate_envelope(ref, _ctx(ref), tx_conn))["verdict"] == "DENY"   # consumed


# --- unknown type / aggregation ---
async def test_unknown_required_denies(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[{"type": "frobnicate", "required": True}], validity={})
    assert (await evaluate_envelope(ref, _ctx(ref), tx_conn))["verdict"] == "DENY"


async def test_unknown_not_required_ignored(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[{"type": "frobnicate", "required": False}], validity={})
    assert (await evaluate_envelope(ref, _ctx(ref), tx_conn))["verdict"] == "ALLOW"


async def test_aggregate_one_deny_is_deny(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "allowed_domains", "value": ["ok.example.com"], "required": True},
        {"type": "max_transaction_value", "value": 100, "currency": "USD", "required": True}], validity={})
    # domain ok (ALLOW), aber value 999 > 100 (DENY) -> aggregiert DENY
    res = await evaluate_envelope(ref, _ctx(ref, domain="ok.example.com", value=999, currency="USD",
                                            value_source="rail_verified"), tx_conn)
    assert res["verdict"] == "DENY"


# --- revocation_check deferred ---
async def test_revocation_check_deferred_denies(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[],
                                 validity={"revocation_check": "https://api.moltrust.ch/aae/rev/{id}"})
    assert (await evaluate_envelope(ref, _ctx(ref), tx_conn))["verdict"] == "DENY"


# --- signiertes eval-row verifizierbar (in-memory + aus DB rekonstruiert) ---
async def test_eval_row_signed_and_verifiable(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[], validity={})
    res = await evaluate_envelope(ref, _ctx(ref), tx_conn)
    assert verify_verdict(res["record"], res["verdict_signature"]) is True
    row = await tx_conn.fetchrow("SELECT * FROM aae_evaluations WHERE eval_id=$1", res["eval_id"])
    rebuilt = {
        "eval_id": row["eval_id"], "aae_ref": row["aae_ref"], "agent_did": row["agent_did"],
        "action_context": json.loads(row["action_context"]), "evaluations": json.loads(row["evaluations"]),
        "verdict": row["verdict"], "value_source": row["value_source"],
        "evaluator_version": row["evaluator_version"],
        # robust: signierter timestamp liegt verbatim im gespeicherten action_context (kein isoformat-re-parse)
        "timestamp": json.loads(row["action_context"])["timestamp"],
        "nonce": row["nonce"],
    }
    assert verify_verdict(rebuilt, row["verdict_signature"]) is True  # gespeicherte Row re-verifizierbar


# --- Fixes aus Security-Code-Review ---
def test_advisory_key_no_colon_collision():
    # DIDs + sha256-refs enthalten ':' -> colon-concat waere kollisionsanfaellig; null-byte verhindert das.
    from app.enforcement.evaluator import _advisory_sql_key
    assert _advisory_sql_key("did:x", "a:b") != _advisory_sql_key("did:x:a", "b")


async def test_fail_closed_on_handler_exception(tx_conn, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(evaluator, "_eval_max_transaction_value", _boom)
    ref = await _insert_envelope(tx_conn, constraints=[
        {"type": "max_transaction_value", "value": 100, "currency": "USD", "required": False}], validity={})
    res = await evaluate_envelope(ref, _ctx(ref, value=50, currency="USD", value_source="rail_verified"), tx_conn)
    assert res["verdict"] == "DENY"  # required=False, aber Handler-Crash -> fail-closed DENY


async def test_per_envelope_rate_limit_isolation(tx_conn):
    rl = [{"type": "rate_limit", "value": 1, "window": "PT1H", "required": False}]
    refA = await _insert_envelope(tx_conn, constraints=rl, validity={})
    refB = await _insert_envelope(tx_conn, constraints=rl, validity={})
    assert (await evaluate_envelope(refA, _ctx(refA), tx_conn))["verdict"] == "ALLOW"  # A count 0
    assert (await evaluate_envelope(refA, _ctx(refA), tx_conn))["verdict"] == "DENY"   # A count 1 >= 1
    assert (await evaluate_envelope(refB, _ctx(refB), tx_conn))["verdict"] == "ALLOW"  # B eigener scope -> Isolation


async def test_server_overrides_client_timestamp(tx_conn):
    ref = await _insert_envelope(tx_conn, constraints=[], validity={})
    # client versucht backdating; Server muss es ueberschreiben
    res = await evaluate_envelope(ref, _ctx(ref, timestamp="1999-01-01T00:00:00Z"), tx_conn)
    row = await tx_conn.fetchrow("SELECT action_context FROM aae_evaluations WHERE eval_id=$1", res["eval_id"])
    stored_ts = json.loads(row["action_context"])["timestamp"]
    assert not stored_ts.startswith("1999")  # client-timestamp verworfen, Server-Zeit gilt


# --- echter Parallel-TOCTOU: advisory-lock serialisiert (HINTERLAESST test-markierte Rows) ---
async def test_parallel_eval_toctou_single_use():
    setup = await asyncpg.connect(**DB)
    ref = await _insert_envelope(setup, constraints=[], validity={"single_use": True},
                                 aae_id=f"test:vc:par{uuid.uuid4().hex[:8]}")
    await setup.close()

    async def _run():
        c = await asyncpg.connect(**DB)
        try:
            return await evaluate_envelope(ref, _ctx(ref), c)
        finally:
            await c.close()

    r1, r2 = await asyncio.gather(_run(), _run())
    # advisory-lock serialisiert -> genau ein ALLOW (erste Nutzung), ein DENY (consumed)
    assert sorted([r1["verdict"], r2["verdict"]]) == ["ALLOW", "DENY"]
