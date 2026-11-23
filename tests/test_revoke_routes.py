"""Tests — a revoked DID cannot write through its API key (ops REVOKE-ROUTES).

/vc/aae/evaluate refuses an API-key call whose DID is revoked (#1063). The
same refusal, from the same function (`did_revoked_refusal` in app/main.py),
now guards three more routes that write on behalf of the key's owner:
POST /vc/ipr/submit, POST /vc/ipr/{ipr_id}/outcome and POST /board.

Each route gets two tests: a revoked DID gets 403 with code did_revoked and
nothing is written; an active DID passes the check and the write lands.
"""
import os
import uuid

import asyncpg
import pytest
import pytest_asyncio


REASON = "test: revoke-routes"


async def _conn():
    return await asyncpg.connect(host="localhost", database=os.getenv("DB_NAME", "moltstack"),
                                 user="moltstack")


async def _revoke(did):
    conn = await _conn()
    try:
        await conn.execute(
            "UPDATE agents SET revoked_at = now(), revocation_reason = $2 WHERE did = $1",
            did, REASON)
    finally:
        await conn.close()


def _hash():
    return "sha256:" + uuid.uuid4().hex + uuid.uuid4().hex


def _assert_refused(r):
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["code"] == "did_revoked"
    assert body["revoked"]["reason"] == REASON and body["revoked"]["at"]


@pytest_asyncio.fixture
async def own_rows():
    """DIDs whose IPR and board rows this module removes afterwards."""
    dids: list[str] = []
    yield dids
    if not dids:
        return
    conn = await _conn()
    try:
        await conn.execute("DELETE FROM interaction_proof_records WHERE agent_did = ANY($1::text[])", dids)
        await conn.execute("DELETE FROM board_entries WHERE did = ANY($1::text[])", dids)
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def no_rate_limit():
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


# ── POST /vc/ipr/submit ─────────────────────────────────────────────────────

def _ipr(did):
    return {"agent_did": did, "output_hash": _hash(), "confidence": 0.5,
            "agent_signature": "test-signature-revoke-routes",
            "produced_at": "2026-11-23T12:00:00Z"}


async def _ipr_state(did):
    conn = await _conn()
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM interaction_proof_records WHERE agent_did = $1", did)
    finally:
        await conn.close()


async def test_submit_refuses_revoked_did(async_client, credit_test_agent, own_rows):
    did, key = await credit_test_agent()
    own_rows.append(did)
    await _revoke(did)
    r = await async_client.post("/vc/ipr/submit", json=_ipr(did), headers={"X-API-Key": key})
    _assert_refused(r)
    assert await _ipr_state(did) == 0


async def test_submit_lets_active_did_through(async_client, credit_test_agent, own_rows):
    did, key = await credit_test_agent()
    own_rows.append(did)
    r = await async_client.post("/vc/ipr/submit", json=_ipr(did), headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    assert await _ipr_state(did) == 1


# ── POST /vc/ipr/{ipr_id}/outcome ───────────────────────────────────────────

async def _record_for(did):
    conn = await _conn()
    try:
        return str(await conn.fetchval(
            """INSERT INTO interaction_proof_records
               (agent_did, output_hash, confidence, agent_signature, produced_at)
               VALUES ($1, $2, 0.5, 'test-signature', now()) RETURNING id""",
            did, _hash()))
    finally:
        await conn.close()


async def _outcome_of(rid):
    conn = await _conn()
    try:
        return await conn.fetchval(
            "SELECT outcome_hash FROM interaction_proof_records WHERE id = $1", uuid.UUID(rid))
    finally:
        await conn.close()


async def test_outcome_refuses_revoked_did(async_client, credit_test_agent, own_rows):
    did, key = await credit_test_agent()
    own_rows.append(did)
    rid = await _record_for(did)
    await _revoke(did)
    r = await async_client.post(f"/vc/ipr/{rid}/outcome",
                                json={"outcome_hash": _hash(), "outcome_correct": True},
                                headers={"X-API-Key": key})
    _assert_refused(r)
    assert await _outcome_of(rid) is None


async def test_outcome_refuses_revoked_did_before_record_lookup(async_client, credit_test_agent):
    """A revoked DID gets did_revoked, not 404, for an id that does not exist."""
    did, key = await credit_test_agent()
    await _revoke(did)
    r = await async_client.post(f"/vc/ipr/{uuid.uuid4()}/outcome",
                                json={"outcome_hash": _hash(), "outcome_correct": True},
                                headers={"X-API-Key": key})
    _assert_refused(r)


async def test_outcome_lets_active_did_through(async_client, credit_test_agent, own_rows):
    did, key = await credit_test_agent()
    own_rows.append(did)
    rid = await _record_for(did)
    h = _hash()
    r = await async_client.post(f"/vc/ipr/{rid}/outcome",
                                json={"outcome_hash": h, "outcome_correct": True},
                                headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["outcome_recorded"] is True
    assert await _outcome_of(rid) == h


# ── POST /board ─────────────────────────────────────────────────────────────

def _entry(did):
    return {"did": did, "offers": ["endorsement"], "seeks": ["data"], "note": "revoke-routes test"}


async def _board_rows(did):
    conn = await _conn()
    try:
        return await conn.fetchval("SELECT count(*) FROM board_entries WHERE did = $1", did)
    finally:
        await conn.close()


async def test_board_refuses_revoked_did(async_client, credit_test_agent, own_rows):
    did, key = await credit_test_agent()
    own_rows.append(did)
    await _revoke(did)
    r = await async_client.post("/board", json=_entry(did), headers={"X-API-Key": key})
    _assert_refused(r)
    assert await _board_rows(did) == 0


async def test_board_lets_active_did_through(async_client, credit_test_agent, own_rows):
    did, key = await credit_test_agent()
    own_rows.append(did)
    r = await async_client.post("/board", json=_entry(did), headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "posted"
    assert await _board_rows(did) == 1
