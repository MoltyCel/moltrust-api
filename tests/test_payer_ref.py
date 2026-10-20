"""Phase 2 payer_ref — account mint, slot quota, count-gate 402, credits bypass.

Runs against moltstack_sandbox (conftest default). Cleans up its own rows.
"""
import uuid
import pytest
import pytest_asyncio

import app.main as _m
from app.main import API_KEYS
from app import accounts


def _pool():
    # Resolve the live pool at call-time (module-load value is None until lifespan).
    return _m.db_pool


@pytest_asyncio.fixture
async def payer_env(app_with_lifespan):
    db_pool = _pool()
    keys, payers, dids, subs = [], [], [], []

    async def mk_account(email=None):
        key = f"mt_tc_{uuid.uuid4().hex}"
        # Public-provider domain => the register endpoint's per-domain Sybil gate
        # is skipped (it isn't the subject here), so the slot gate is reached.
        email = email or f"tc{uuid.uuid4().hex[:10]}@gmail.com"
        async with _pool().acquire() as conn:
            await conn.execute("INSERT INTO api_keys (key, email) VALUES ($1, $2)", key, email)
            pr = await accounts.create_account_for_key(conn, key, email)
        API_KEYS.add(key)
        keys.append(key)
        payers.append(pr)
        return key, pr

    async def add_sub(payer_ref, tier="base", active=True):
        sub_id = f"sub_tc_{uuid.uuid4().hex[:12]}"
        async with _pool().acquire() as conn:
            await conn.execute(
                "INSERT INTO billing_subscriptions "
                "(stripe_subscription_id, stripe_customer_id, tier, payer_ref, active) "
                "VALUES ($1, $2, $3, $4, $5)",
                sub_id, f"cus_tc_{uuid.uuid4().hex[:8]}", tier, payer_ref, active,
            )
        subs.append(sub_id)
        return sub_id

    async def link(did, payer_ref):
        async with _pool().acquire() as conn:
            await accounts.link_agent(conn, did, payer_ref)
        dids.append(did)

    env = type("Env", (), {
        "mk_account": staticmethod(mk_account),
        "add_sub": staticmethod(add_sub),
        "link": staticmethod(link),
    })
    yield env

    async with _pool().acquire() as conn:
        for d in dids:
            await conn.execute("DELETE FROM agent_payer WHERE did = $1", d)
            await conn.execute("DELETE FROM payer_usage_meter WHERE did = $1", d)
        for sid in subs:
            await conn.execute("DELETE FROM billing_subscriptions WHERE stripe_subscription_id = $1", sid)
        for k in keys:
            await conn.execute("DELETE FROM api_keys WHERE key = $1", k)
            API_KEYS.discard(k)
        for pr in payers:
            await conn.execute("DELETE FROM payer_usage_meter WHERE payer_ref = $1", pr)
            await conn.execute("DELETE FROM accounts WHERE payer_ref = $1", pr)


# 1 — account minted at key issuance, idempotent (no backfill / double-mint)
async def test_account_minted_and_idempotent(payer_env):
    key, pr = await payer_env.mk_account()
    assert pr and pr.startswith("pyr_")
    async with _pool().acquire() as conn:
        again = await accounts.create_account_for_key(conn, key, "other@test.local")
        assert again == pr
        assert await accounts.payer_ref_for_key(conn, key) == pr


# 2 — quota is the SUM over active subs; 0 when none (free)
async def test_slot_quota_sums_active_subs(payer_env):
    key, pr = await payer_env.mk_account()
    async with _pool().acquire() as conn:
        assert await accounts.slot_quota(conn, pr) == 0
    await payer_env.add_sub(pr, "base")
    async with _pool().acquire() as conn:
        assert await accounts.slot_quota(conn, pr) == 2
    await payer_env.add_sub(pr, "slot")
    async with _pool().acquire() as conn:
        assert await accounts.slot_quota(conn, pr) == 3
    # inactive sub must not count
    await payer_env.add_sub(pr, "base", active=False)
    async with _pool().acquire() as conn:
        assert await accounts.slot_quota(conn, pr) == 3


# 3 — 3rd agent under a 2-slot Base account gets 402 at /identity/register
async def test_count_gate_402_on_third_agent(payer_env, async_client):
    key, pr = await payer_env.mk_account()
    await payer_env.add_sub(pr, "base")  # quota = 2
    for _ in range(2):
        await payer_env.link(f"did:moltrust:{uuid.uuid4().hex[:16]}", pr)

    resp = await async_client.post(
        "/identity/register",
        headers={"X-API-Key": key},
        json={"display_name": f"tc-{uuid.uuid4().hex[:6]}", "platform": "test"},
    )
    assert resp.status_code == 402, f"expected 402, got {resp.status_code}: {resp.text[:400]}"
    assert "slot_limit_reached" in resp.text


# 4 — an account WITH quota headroom is NOT slot-gated (gate returns non-402)
async def test_under_quota_not_slot_gated(payer_env, async_client):
    key, pr = await payer_env.mk_account()
    await payer_env.add_sub(pr, "base")  # quota = 2
    await payer_env.link(f"did:moltrust:{uuid.uuid4().hex[:16]}", pr)  # 1 used < 2

    resp = await async_client.post(
        "/identity/register",
        headers={"X-API-Key": key},
        json={"display_name": f"tc-{uuid.uuid4().hex[:6]}", "platform": "test"},
    )
    # must not be blocked by the slot gate (may 200, or fail later on anchoring —
    # what matters is it is NOT a slot_limit_reached 402)
    assert not (resp.status_code == 402 and "slot_limit_reached" in resp.text), (
        f"under-quota register was slot-gated: {resp.status_code} {resp.text[:300]}"
    )


# 5 — paid tier: NO credit deduct, metering row written
async def test_credits_bypass_paid_no_deduct(payer_env, async_client, credit_test_agent):
    did, key = await credit_test_agent(balance=5)
    _, pr = await payer_env.mk_account()
    await payer_env.add_sub(pr, "base")
    await payer_env.link(did, pr)

    resp = await async_client.get(f"/identity/verify/{did}", headers={"X-API-Key": key})
    assert resp.status_code < 400, f"{resp.status_code} {resp.text[:200]}"
    async with _pool().acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM credit_balances WHERE did = $1", did)
        assert bal == 5, f"paid tier must not deduct; balance={bal}"
        calls = await conn.fetchval("SELECT calls FROM payer_usage_meter WHERE did = $1", did)
        assert calls == 1, f"metered call expected; got {calls}"


# 6 — control: free agent (no payer link) still deducts normally
async def test_free_agent_still_deducts(async_client, credit_test_agent):
    did, key = await credit_test_agent(balance=5)  # no account/payer link
    resp = await async_client.get(f"/identity/verify/{did}", headers={"X-API-Key": key})
    assert resp.status_code < 400
    async with _pool().acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM credit_balances WHERE did = $1", did)
        assert bal == 4, f"free tier must deduct; balance={bal}"
