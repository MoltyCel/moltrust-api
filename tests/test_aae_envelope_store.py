"""Tests — AAE Envelope Store (D3 Komponente 1, App-Layer).

Die aae_envelopes-Tabelle ist append-only (UPDATE/DELETE geblockt), daher KEIN
DELETE-Cleanup: jeder Test läuft in einer Transaktion, die am Ende zurückgerollt
wird (rollback entfernt Testzeilen trotz Immutability).
"""
import json
import os
import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.enforcement import envelope_store as store
from app.enforcement.envelope_store import EnvelopeValidationError, canonical_scope


@pytest_asyncio.fixture
async def tx_conn():
    conn = await asyncpg.connect(
        host="localhost", database=os.getenv("DB_NAME", "moltstack"), user="moltstack"
    )
    tr = conn.transaction()
    await tr.start()
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


def _env(aae_id=None, scope=None):
    aae_id = aae_id or f"test:vc:{uuid.uuid4().hex[:12]}"
    scope = scope if scope is not None else ["payments:write", "data:read"]
    mandate = {"scope": scope}
    constraints = [
        {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True},
        {"type": "rate_limit", "value": 10, "window": "PT1H", "required": False},
    ]
    validity = {"not_before": "2026-06-01T00:00:00Z", "not_after": "2026-06-01T18:00:00Z"}
    raw_envelope = {"mandate": mandate, "constraints": constraints, "validity": validity, "aae_id": aae_id}
    return dict(
        aae_id=aae_id, issuer_did="did:moltrust:test_issuer", envelope_signature="testsig",
        mandate=mandate, constraints=constraints, validity=validity,
        aae_version="1.0", taxonomy_version="1.0", raw_envelope=raw_envelope,
    )


async def test_roundtrip_insert_and_read(tx_conn):
    env = _env()
    aae_ref = await store.persist_envelope(tx_conn, **env)
    assert aae_ref.startswith("sha256:")
    row = await tx_conn.fetchrow("SELECT * FROM aae_envelopes WHERE aae_ref=$1", aae_ref)
    assert row is not None
    assert row["aae_id"] == env["aae_id"]
    assert json.loads(row["validity"])["not_after"] == "2026-06-01T18:00:00Z"
    # Hash-Bindung: aae_ref == sha256(raw_canonical), serverseitig vom Trigger gesetzt
    expected = await tx_conn.fetchval(
        "SELECT 'sha256:'||encode(digest($1::bytea,'sha256'),'hex')",
        store.canonical_raw(env["raw_envelope"]),
    )
    assert aae_ref == expected


async def test_jcs_idempotence_scope(tx_conn):
    # semantisch gleicher scope (andere key-order) -> identische canonical bytes
    assert canonical_scope({"a": 1, "b": 2}) == canonical_scope({"b": 2, "a": 1})


async def test_single_use_unique_collision(tx_conn):
    aae_id = f"test:vc:{uuid.uuid4().hex[:12]}"
    env1 = _env(aae_id=aae_id, scope=["x"])
    await store.persist_envelope(tx_conn, **env1)
    env2 = _env(aae_id=aae_id, scope=["x"])
    env2["raw_envelope"] = {**env2["raw_envelope"], "nonce": "different"}  # anderer aae_ref (PK), gleicher (aae_id, scope)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with tx_conn.transaction():
            await store.persist_envelope(tx_conn, **env2)


async def test_immutability_update_blocked(tx_conn):
    env = _env()
    aae_ref = await store.persist_envelope(tx_conn, **env)
    with pytest.raises(asyncpg.PostgresError):
        async with tx_conn.transaction():
            await tx_conn.execute("UPDATE aae_envelopes SET aae_id='hacked' WHERE aae_ref=$1", aae_ref)


async def test_transaction_rollback_on_delegation_failure(tx_conn):
    env = _env()
    # parent_did=None verletzt NOT NULL in agent_delegations -> ganze Transaktion rollt zurück
    with pytest.raises(asyncpg.PostgresError):
        await store.persist_with_delegation(
            tx_conn, parent_did=None, child_did="did:moltrust:test_child",
            credential_type="AAE", hop_depth=1, **env,
        )
    cnt = await tx_conn.fetchval("SELECT count(*) FROM aae_envelopes WHERE aae_id=$1", env["aae_id"])
    assert cnt == 0  # Envelope-INSERT wurde mit-zurückgerollt (no-FK-Mitigation)


def test_validate_constraints_rejects_bad_shape():
    with pytest.raises(EnvelopeValidationError):
        store.validate_constraints([{"value": 1}])  # missing 'type'
    with pytest.raises(EnvelopeValidationError):
        store.validate_constraints([{"type": "max_transaction_value", "value": 1}])  # missing 'currency'
    with pytest.raises(EnvelopeValidationError):
        store.validate_constraints("not-a-list")


def test_json_depth_limit():
    deep = {}
    cur = deep
    for _ in range(40):
        cur["x"] = {}
        cur = cur["x"]
    with pytest.raises(EnvelopeValidationError):
        store.validate_envelope(deep, [], {})
