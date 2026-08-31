"""FIX 7b (H6) — sports commits may only be made under the caller's own DID.

verify_api_key only answers "is this one of the platform keys". It does not say
which agent is calling, so without an explicit ownership check any key holder
could commit predictions under a foreign agent_did — and prediction_bonus feeds
the trust score.

anchor_to_base is stubbed throughout: it signs and broadcasts a real Base
transaction from BASE_WALLET_KEY, so an unstubbed positive-path test would spend
actual gas.
"""
import datetime
import uuid

import pytest


@pytest.fixture(autouse=True)
def _no_anchor(monkeypatch):
    """Never let a test broadcast an on-chain transaction."""
    import app.main as m

    async def _stub(agent_did: str, timestamp: str):
        return None

    monkeypatch.setattr(m, "anchor_to_base", _stub)
    monkeypatch.setattr(m.limiter, "enabled", False, raising=False)


def _future() -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
    return dt.isoformat().replace("+00:00", "Z")


def _event_id() -> str:
    return f"test-evt-{uuid.uuid4().hex[:12]}"


async def _cleanup_prediction(event_id: str):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sports_predictions WHERE event_id LIKE $1", f"%{event_id}%")


# ---------------------------------------------------------------------------
# Test 1 — the IDOR: committing under someone else's DID
# ---------------------------------------------------------------------------
async def test_commit_under_foreign_did_is_refused(async_client, credit_test_agent):
    from app.main import db_pool

    victim_did, _ = await credit_test_agent(balance=1000)
    _, attacker_key = await credit_test_agent(balance=1000)

    event_id = _event_id()
    resp = await async_client.post(
        "/sports/predictions/commit",
        json={
            "agent_did": victim_did,
            "event_id": event_id,
            "prediction": {"winner": "home"},
            "event_start": _future(),
        },
        headers={"X-API-Key": attacker_key},
    )

    assert resp.status_code == 403, f"expected 403, got {resp.status_code} {resp.text[:200]}"
    assert "does not own" in resp.text

    async with db_pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM sports_predictions WHERE agent_did = $1 AND event_id LIKE $2",
            victim_did, f"%{event_id}%",
        )
    assert row is None, "a refused commit must not write a prediction row"


# ---------------------------------------------------------------------------
# Test 2 — own DID still commits
# ---------------------------------------------------------------------------
async def test_commit_under_own_did_succeeds(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)

    event_id = _event_id()
    try:
        resp = await async_client.post(
            "/sports/predictions/commit",
            json={
                "agent_did": did,
                "event_id": event_id,
                "prediction": {"winner": "away"},
                "event_start": _future(),
            },
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code < 400, f"{resp.status_code} {resp.text[:300]}"
        assert resp.json()["agent_did"] == did
    finally:
        await _cleanup_prediction(event_id)


# ---------------------------------------------------------------------------
# Test 3 — signal-provider registration under a foreign DID
# ---------------------------------------------------------------------------
async def test_signal_register_under_foreign_did_is_refused(async_client, credit_test_agent):
    victim_did, _ = await credit_test_agent(balance=1000)
    _, attacker_key = await credit_test_agent(balance=1000)

    resp = await async_client.post(
        "/sports/signals/register",
        json={
            "agent_did": victim_did,
            "provider_name": f"tc-provider-{uuid.uuid4().hex[:8]}",
            "sport_focus": ["soccer"],
        },
        headers={"X-API-Key": attacker_key},
    )

    assert resp.status_code == 403, f"expected 403, got {resp.status_code} {resp.text[:200]}"


# ---------------------------------------------------------------------------
# Test 4 — fantasy lineup under a foreign DID
# ---------------------------------------------------------------------------
async def test_fantasy_commit_under_foreign_did_is_refused(async_client, credit_test_agent):
    victim_did, _ = await credit_test_agent(balance=1000)
    _, attacker_key = await credit_test_agent(balance=1000)

    resp = await async_client.post(
        "/sports/fantasy/lineups/commit",
        json={
            "agent_did": victim_did,
            "contest_id": f"test-c-{uuid.uuid4().hex[:8]}",
            "platform": "custom",
            "sport": "soccer",
            "contest_start_iso": _future(),
            "lineup": {"players": ["a", "b"]},
        },
        headers={"X-API-Key": attacker_key},
    )

    assert resp.status_code == 403, f"expected 403, got {resp.status_code} {resp.text[:200]}"
