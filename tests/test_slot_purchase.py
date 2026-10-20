"""C — $9 slot purchase: quota additive over active slot-subs, count-gate follows.
D — AWS: customer_identifier column present + persistable.

Sandbox only (conftest default). Subs are injected directly into
billing_subscriptions (tier='slot') — no real Stripe, no fake finance in prod.
"""
import uuid
import pytest
import pytest_asyncio

import app.main as _m
from app.main import API_KEYS
from app import accounts
from app.billing import TIERS, SLOT_LOOKUP_KEY


def _pool():
    return _m.db_pool


@pytest_asyncio.fixture
async def payer_env(app_with_lifespan):
    keys, payers, dids, subs = [], [], [], []

    async def mk_account(email=None):
        key = f"mt_tc_{uuid.uuid4().hex}"
        email = email or f"tc{uuid.uuid4().hex[:10]}@gmail.com"  # public domain => domain-gate skipped
        async with _pool().acquire() as conn:
            await conn.execute("INSERT INTO api_keys (key, email) VALUES ($1, $2)", key, email)
            pr = await accounts.create_account_for_key(conn, key, email)
        API_KEYS.add(key)
        keys.append(key); payers.append(pr)
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
        "mk_account": staticmethod(mk_account), "add_sub": staticmethod(add_sub),
        "link": staticmethod(link),
    })
    yield env

    async with _pool().acquire() as conn:
        for d in dids:
            await conn.execute("DELETE FROM agent_payer WHERE did = $1", d)
        for sid in subs:
            await conn.execute("DELETE FROM billing_subscriptions WHERE stripe_subscription_id = $1", sid)
        for k in keys:
            await conn.execute("DELETE FROM api_keys WHERE key = $1", k)
            API_KEYS.discard(k)
        for pr in payers:
            await conn.execute("DELETE FROM agent_payer WHERE payer_ref = $1", pr)
            await conn.execute("DELETE FROM accounts WHERE payer_ref = $1", pr)


def _slot_gated(resp):
    return resp.status_code == 402 and "slot_limit_reached" in resp.text


async def _register(async_client, key):
    return await async_client.post(
        "/identity/register",
        headers={"X-API-Key": key},
        json={"display_name": f"tc-{uuid.uuid4().hex[:6]}", "platform": "test"},
    )


# C-1 — slot is now a real tier bound to the load-bearing lookup key
async def test_slot_tier_registered():
    assert "slot" in TIERS
    assert TIERS["slot"]["lookup_key"] == SLOT_LOOKUP_KEY == "mt_v2_slot_monthly"
    assert accounts.SLOT_VALUE["slot"] == 1


# C-2 — quota is additive over active slot-subs
async def test_quota_additive_over_slots(payer_env):
    _, pr = await payer_env.mk_account()
    await payer_env.add_sub(pr, "base")
    async with _pool().acquire() as conn:
        assert await accounts.slot_quota(conn, pr) == 2
    await payer_env.add_sub(pr, "slot")
    async with _pool().acquire() as conn:
        assert await accounts.slot_quota(conn, pr) == 3
    await payer_env.add_sub(pr, "slot")
    async with _pool().acquire() as conn:
        assert await accounts.slot_quota(conn, pr) == 4


# C-3 — 3rd agent 402; buy a slot; 3rd now passes
async def test_third_402_then_slot_allows(payer_env, async_client):
    key, pr = await payer_env.mk_account()
    await payer_env.add_sub(pr, "base")  # quota 2
    for _ in range(2):
        await payer_env.link(f"did:moltrust:{uuid.uuid4().hex[:16]}", pr)

    r = await _register(async_client, key)
    assert _slot_gated(r), f"3rd agent should be 402: {r.status_code} {r.text[:200]}"

    await payer_env.add_sub(pr, "slot")  # quota 3
    r2 = await _register(async_client, key)
    assert not _slot_gated(r2), f"after slot, 3rd agent must pass gate: {r2.status_code} {r2.text[:200]}"


# C-4 — 4th agent 402; two slots => 4 agents allowed
async def test_fourth_402_two_slots_four_agents(payer_env, async_client):
    key, pr = await payer_env.mk_account()
    await payer_env.add_sub(pr, "base")   # 2
    await payer_env.add_sub(pr, "slot")   # +1 => 3
    for _ in range(3):
        await payer_env.link(f"did:moltrust:{uuid.uuid4().hex[:16]}", pr)

    r = await _register(async_client, key)
    assert _slot_gated(r), f"4th agent at quota 3 should be 402: {r.status_code} {r.text[:200]}"

    await payer_env.add_sub(pr, "slot")   # +1 => 4
    r2 = await _register(async_client, key)
    assert not _slot_gated(r2), f"with 2 slots (quota 4), 4th agent must pass: {r2.status_code} {r2.text[:200]}"


# D — customer_identifier column exists and both AWS values persist together
async def test_aws_customer_identifier_column(app_with_lifespan):
    async with _pool().acquire() as conn:
        col = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='aws_marketplace_subscribers' AND column_name='customer_identifier'"
        )
        assert col == "customer_identifier", "D migration/ensure did not add the column"
        # round-trip both values (cleanup after)
        acct = f"tc-aws-{uuid.uuid4().hex[:8]}"
        try:
            await conn.execute(
                "INSERT INTO aws_marketplace_subscribers "
                "(customer_aws_account_id, customer_identifier, product_code) VALUES ($1,$2,$3)",
                acct, f"ci-{uuid.uuid4().hex[:8]}", "74az0btybm649octamy0sktos",
            )
            row = await conn.fetchrow(
                "SELECT customer_aws_account_id, customer_identifier "
                "FROM aws_marketplace_subscribers WHERE customer_aws_account_id=$1", acct)
            assert row["customer_identifier"] and row["customer_aws_account_id"] == acct
        finally:
            await conn.execute("DELETE FROM aws_marketplace_subscribers WHERE customer_aws_account_id=$1", acct)
