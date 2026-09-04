"""Phase 3 MITTEL bundle — M1, M2, M3, M4, M11.

Runs against the sandbox database like the rest of the suite.
"""
import os
import uuid

import pytest


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """These tests exercise the guards, not the limiter buckets."""
    import app.main as m
    monkeypatch.setattr(m.limiter, "enabled", False, raising=False)


# ---------------------------------------------------------------------------
# M1 — /caep/acknowledge is scoped to the DID the event belongs to
# ---------------------------------------------------------------------------
async def _emit(did: str) -> str:
    from app.main import db_pool
    from app.caep import emit_caep_event
    async with db_pool.acquire() as conn:
        return await emit_caep_event(conn, did, "trust_score_change", {"delta": -12})


async def _cleanup_events(did: str):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM caep_events WHERE did = $1", did)


async def test_acknowledge_refuses_a_foreign_event(async_client, credit_test_agent):
    victim_did, _ = await credit_test_agent(balance=10)
    _, attacker_key = await credit_test_agent(balance=10)

    event_id = await _emit(victim_did)
    try:
        resp = await async_client.post(
            f"/caep/acknowledge/{event_id}",
            headers={"X-API-Key": attacker_key},
        )
        assert resp.status_code == 403, f"{resp.status_code} {resp.text[:200]}"

        from app.main import db_pool
        async with db_pool.acquire() as conn:
            acked = await conn.fetchval(
                "SELECT acknowledged_at FROM caep_events WHERE event_id = $1", event_id
            )
        assert acked is None, "a refused acknowledge still suppressed the event"
    finally:
        await _cleanup_events(victim_did)


async def test_acknowledge_accepts_the_owning_did(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=10)
    event_id = await _emit(did)
    try:
        resp = await async_client.post(
            f"/caep/acknowledge/{event_id}",
            headers={"X-API-Key": api_key},
        )
        assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
        assert resp.json()["acknowledged"] is True
    finally:
        await _cleanup_events(did)


async def test_acknowledge_requires_an_api_key(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=10)
    event_id = await _emit(did)
    try:
        resp = await async_client.post(f"/caep/acknowledge/{event_id}")
        assert resp.status_code in (401, 422), f"{resp.status_code} {resp.text[:200]}"
    finally:
        await _cleanup_events(did)


# ---------------------------------------------------------------------------
# M2 — DID validation on the unauthenticated read surfaces
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/skill/trust-score/{did}",
    "/swarm/graph/{did}",
    "/trust/gate/{did}",
])
async def test_junk_did_is_rejected(async_client, path):
    junk = "not-a-did-" + "x" * 200
    resp = await async_client.get(path.format(did=junk))
    assert resp.status_code == 400, f"{path}: {resp.status_code} {resp.text[:200]}"


async def test_valid_did_still_reaches_the_handler(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=10)
    resp = await async_client.get(f"/skill/trust-score/{did}")
    assert resp.status_code < 500, f"{resp.status_code} {resp.text[:200]}"


# ---------------------------------------------------------------------------
# M3 — global body size limit
# ---------------------------------------------------------------------------
async def test_oversized_body_is_refused(async_client):
    from app.main import MAX_REQUEST_BODY_BYTES
    payload = "x" * (MAX_REQUEST_BODY_BYTES + 1024)
    resp = await async_client.post(
        "/vc/aae/submit",
        content=payload.encode(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413, f"{resp.status_code} {resp.text[:200]}"


async def test_normal_body_passes_the_limit(async_client):
    resp = await async_client.post(
        "/vc/aae/submit",
        json={"hello": "world"},
    )
    # Anything but 413 — the endpoint may well reject the payload on its merits.
    assert resp.status_code != 413, resp.text[:200]


async def test_bogus_content_length_is_refused(async_client):
    resp = await async_client.post(
        "/vc/aae/submit",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "not-a-number"},
    )
    assert resp.status_code in (400, 413), f"{resp.status_code} {resp.text[:200]}"


# ---------------------------------------------------------------------------
# M11 / M4 — constant-time comparisons
# ---------------------------------------------------------------------------
def test_api_key_check_still_accepts_and_rejects():
    """M11 changed how the comparison is done, not what it decides."""
    from fastapi import HTTPException
    from app.main import verify_api_key, API_KEYS

    key = "mt_unit_" + uuid.uuid4().hex
    API_KEYS.add(key)
    try:
        assert verify_api_key(key) == key

        with pytest.raises(HTTPException) as unknown:
            verify_api_key("mt_definitely_not_valid")
        assert unknown.value.status_code == 403

        with pytest.raises(HTTPException) as too_long:
            verify_api_key("x" * 200)
        assert too_long.value.status_code == 403

        # A prefix of a valid key must not pass.
        with pytest.raises(HTTPException):
            verify_api_key(key[:-1])
    finally:
        API_KEYS.discard(key)


def test_no_plain_equality_left_on_admin_key():
    """M4 — every ADMIN_KEY comparison goes through compare_digest."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, "app", "main.py")).read()
    assert "admin_key == expected" not in source
    assert "admin_key == expected_admin" not in source


def test_verify_api_key_does_not_use_set_membership():
    """M11 — the set lookup short-circuits; the loop must not."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, "app", "main.py")).read()
    start = source.index("def verify_api_key(")
    body = source[start : start + 900]
    assert "x_api_key not in API_KEYS" not in body
    assert "compare_digest" in body


# ---------------------------------------------------------------------------
# register-batch — one route, and it is rate limited
# ---------------------------------------------------------------------------
def test_register_batch_is_registered_exactly_once():
    """A second @app.post on the same path shadows the first and its limiter."""
    import app.main as m
    hits = [r for r in m.app.routes if getattr(r, "path", "") == "/identity/register-batch"]
    assert len(hits) == 1, f"{len(hits)} registrations — a duplicate shadows the limiter"


def test_register_batch_carries_a_rate_limit():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, "app", "main.py")).read()
    i = source.index('@app.post("/identity/register-batch"')
    head = source[i : i + 220]
    assert "@limiter.limit" in head, "the surviving route must carry the limit"


async def test_register_batch_still_refuses_without_admin_key(async_client):
    resp = await async_client.post("/identity/register-batch", json={"agents": []})
    assert resp.status_code == 403, f"{resp.status_code} {resp.text[:160]}"
