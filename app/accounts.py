"""Payer edge (Phase 2, 2026-07-16).

An `accounts` row is the paying party, keyed by an opaque internal `payer_ref`
minted at API-key / email signup. It is NOT the DID (agent identity) and NOT the
email (a human handle that can change / be shared across accounts).

The agents table is postgres-owned and cannot be ALTERed by the moltstack role,
so the did<->payer_ref link lives in the role-owned side table `agent_payer`
(not agents.payer_ref). api_keys and billing_subscriptions are moltstack-owned
and take a payer_ref column directly.

Everything here is additive and nullable: existing keys, agents and the whole
bestand have no payer_ref and stay ungated. Only NEW signups mint an account.
"""
import secrets
import logging

log = logging.getLogger("accounts")

# Slots granted by one ACTIVE subscription of each tier. A payer's quota is the
# sum over its active subscriptions: one base (2) plus N slot add-ons (1 each),
# or one scale (75). 0 => no active paid sub => free => ungated by slots.
SLOT_VALUE = {
    "base": 2,
    "scale": 75,
    "slot": 1,
    "slot_addon": 1,
    "slot_monthly": 1,
}


async def ensure_accounts_tables(conn):
    """Idempotent schema. Mirrors app/migrations/012_payer_ref.sql (up)."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            payer_ref               TEXT PRIMARY KEY,
            email                   TEXT,
            stripe_customer_id      TEXT,
            aws_customer_identifier TEXT,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_accounts_email ON accounts(email)")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_accounts_stripe_customer ON accounts(stripe_customer_id)"
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_payer (
            did        TEXT PRIMARY KEY,
            payer_ref  TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_payer_ref ON agent_payer(payer_ref)")
    # Bounded usage meter for paid (bypassed) calls: one row per (payer_ref, did),
    # so metering/visibility stays even though the credit rail is bypassed.
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payer_usage_meter (
            payer_ref    TEXT NOT NULL,
            did          TEXT NOT NULL,
            calls        BIGINT NOT NULL DEFAULT 0,
            metered_cost BIGINT NOT NULL DEFAULT 0,
            last_call    TIMESTAMPTZ,
            PRIMARY KEY (payer_ref, did)
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payer_usage_meter_ref ON payer_usage_meter(payer_ref)"
    )
    # api_keys + billing_subscriptions are moltstack-owned -> ALTER allowed.
    await conn.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS payer_ref TEXT")
    await conn.execute("ALTER TABLE billing_subscriptions ADD COLUMN IF NOT EXISTS payer_ref TEXT")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_billing_sub_payer ON billing_subscriptions(payer_ref)"
    )


def new_payer_ref() -> str:
    return "pyr_" + secrets.token_hex(16)


async def create_account_for_key(conn, api_key, email, aws_customer_identifier=None):
    """Mint a payer_ref, create the account, link it to the api_key.

    Idempotent: if the key already carries a payer_ref, return it unchanged
    (no backfill of other columns onto the bestand).
    """
    existing = await conn.fetchval("SELECT payer_ref FROM api_keys WHERE key = $1", api_key)
    if existing:
        return existing
    payer_ref = new_payer_ref()
    await conn.execute(
        "INSERT INTO accounts (payer_ref, email, aws_customer_identifier) "
        "VALUES ($1, $2, $3) ON CONFLICT (payer_ref) DO NOTHING",
        payer_ref, email, aws_customer_identifier,
    )
    await conn.execute("UPDATE api_keys SET payer_ref = $1 WHERE key = $2", payer_ref, api_key)
    log.info("payer_ref minted for signup key: %s", payer_ref)
    return payer_ref


async def payer_ref_for_key(conn, api_key):
    if not api_key:
        return None
    return await conn.fetchval("SELECT payer_ref FROM api_keys WHERE key = $1", api_key)


async def payer_ref_for_did(conn, did):
    if not did:
        return None
    return await conn.fetchval("SELECT payer_ref FROM agent_payer WHERE did = $1", did)


async def link_agent(conn, did, payer_ref):
    if not (did and payer_ref):
        return
    await conn.execute(
        "INSERT INTO agent_payer (did, payer_ref) VALUES ($1, $2) ON CONFLICT (did) DO NOTHING",
        did, payer_ref,
    )


async def count_agents(conn, payer_ref):
    return await conn.fetchval(
        "SELECT count(*) FROM agent_payer WHERE payer_ref = $1", payer_ref
    ) or 0


async def slot_quota(conn, payer_ref):
    """Sum of slots from the payer's ACTIVE subscriptions. 0 => free (ungated)."""
    rows = await conn.fetch(
        "SELECT tier FROM billing_subscriptions WHERE payer_ref = $1 AND active = true",
        payer_ref,
    )
    return sum(SLOT_VALUE.get((r["tier"] or "").lower(), 0) for r in rows)


async def has_active_paid_sub(conn, payer_ref):
    if not payer_ref:
        return False
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM billing_subscriptions WHERE payer_ref = $1 AND active = true LIMIT 1",
            payer_ref,
        )
    )


async def active_payer_for_did(conn, did):
    """Return payer_ref iff the agent belongs to an account with an ACTIVE paid
    subscription — the single lookup the credit-bypass needs. None otherwise
    (free / bestand / keyless), so the caller falls through to the credit rail."""
    if not did:
        return None
    return await conn.fetchval(
        "SELECT ap.payer_ref FROM agent_payer ap "
        "JOIN billing_subscriptions bs ON bs.payer_ref = ap.payer_ref AND bs.active = true "
        "WHERE ap.did = $1 LIMIT 1",
        did,
    )


async def meter_paid_call(conn, payer_ref, did, cost):
    """Bump the bounded usage meter for a bypassed paid call (no billing)."""
    await conn.execute(
        "INSERT INTO payer_usage_meter (payer_ref, did, calls, metered_cost, last_call) "
        "VALUES ($1, $2, 1, $3, now()) "
        "ON CONFLICT (payer_ref, did) DO UPDATE SET "
        "calls = payer_usage_meter.calls + 1, "
        "metered_cost = payer_usage_meter.metered_cost + EXCLUDED.metered_cost, "
        "last_call = now()",
        payer_ref, did, cost or 0,
    )


async def bind_stripe_customer(conn, payer_ref, stripe_customer_id):
    if not (payer_ref and stripe_customer_id):
        return
    await conn.execute(
        "UPDATE accounts SET stripe_customer_id = $1 WHERE payer_ref = $2",
        stripe_customer_id, payer_ref,
    )


async def set_aws_identifier(conn, payer_ref, aws_customer_identifier):
    if not (payer_ref and aws_customer_identifier):
        return
    await conn.execute(
        "UPDATE accounts SET aws_customer_identifier = $1 WHERE payer_ref = $2",
        aws_customer_identifier, payer_ref,
    )
