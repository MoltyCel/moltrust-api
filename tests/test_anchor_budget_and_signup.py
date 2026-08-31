"""E3 — daily on-chain anchor budget. E5a — aws_customer_identifier off the signup body."""
import os

import pytest


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.limiter, "enabled", False, raising=False)


async def _reset_budget(count: int = 0):
    from app.main import db_pool, ensure_anchor_budget_table
    async with db_pool.acquire() as conn:
        await ensure_anchor_budget_table(conn)
        await conn.execute(
            """
            INSERT INTO anchor_budget (day, count) VALUES (CURRENT_DATE, $1)
            ON CONFLICT (day) DO UPDATE SET count = $1
            """,
            count,
        )


async def _budget_now() -> int:
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT count FROM anchor_budget WHERE day = CURRENT_DATE")


# ---------------------------------------------------------------------------
# E3
# ---------------------------------------------------------------------------
async def test_slot_is_granted_below_the_budget(app_with_lifespan):
    from app.main import _claim_anchor_slot
    await _reset_budget(0)

    allowed, count = await _claim_anchor_slot()
    assert allowed is True
    assert count == 1


async def test_slot_is_refused_once_the_budget_is_used_up(app_with_lifespan, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "ANCHOR_DAILY_BUDGET", 3)
    await _reset_budget(3)

    allowed, count = await m._claim_anchor_slot()
    assert allowed is False
    assert count == 4, "the counter must keep climbing so the overrun is visible"


async def test_the_last_slot_inside_the_budget_still_passes(app_with_lifespan, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "ANCHOR_DAILY_BUDGET", 3)
    await _reset_budget(2)

    allowed, count = await m._claim_anchor_slot()
    assert allowed is True
    assert count == 3


async def test_anchor_returns_none_when_over_budget(app_with_lifespan, monkeypatch):
    """The commit is still recorded, it just has no on-chain anchor."""
    import app.main as m
    monkeypatch.setattr(m, "ANCHOR_DAILY_BUDGET", 1)
    await _reset_budget(5)

    result = await m.anchor_to_base("did:moltrust:aaaabbbbccccdddd", "2026-09-01T00:00:00Z")
    assert result is None


async def test_budget_survives_a_missing_pool(monkeypatch):
    """A broken counter must not stop anchoring — it must show up in the log."""
    import app.main as m
    monkeypatch.setattr(m, "db_pool", None)
    allowed, count = await m._claim_anchor_slot()
    assert allowed is True
    assert count == 0


def test_budget_default_is_two_hundred():
    import importlib
    import app.main as m
    assert m.ANCHOR_DAILY_BUDGET == int(os.getenv("ANCHOR_DAILY_BUDGET", "200"))


# ---------------------------------------------------------------------------
# E5a
# ---------------------------------------------------------------------------
def test_signup_body_no_longer_carries_the_aws_identifier():
    from app.main import SignupRequest
    assert "aws_customer_identifier" not in SignupRequest.model_fields


async def test_signup_ignores_a_supplied_aws_identifier(async_client):
    """An extra field must not reach create_account_for_key."""
    import uuid
    import app.main as m

    seen = {}

    async def _spy(conn, key, email, aws_id):
        seen["aws_id"] = aws_id
        return "acct_test"

    import app.accounts as accounts
    original = accounts.create_account_for_key
    accounts.create_account_for_key = _spy
    email = f"tc-e5a-{uuid.uuid4().hex[:8]}@test.local"
    try:
        resp = await async_client.post(
            "/auth/signup",
            json={"email": email, "aws_customer_identifier": "aws-victim-12345"},
        )
        assert resp.status_code < 500, resp.text[:200]
        if resp.status_code == 200:
            assert seen.get("aws_id") is None, f"identifier leaked through: {seen}"
    finally:
        accounts.create_account_for_key = original
        async with m.db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_keys WHERE email = $1", email)
