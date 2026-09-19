"""Free tier: what a registered DID gets without paying.

Two allowances, deliberately different in shape.

The hourly one is a call budget: a registered DID may make FREE_CALLS_PER_HOUR
priced calls in a rolling clock hour without touching its credit balance. It
exists so that reading your own agents' state never costs anything — the line
the pricing page draws is that finding out about your own skills is free, and
proving something to someone else is not.

The monthly one is a floor, not a grant. On the first priced call of a calendar
month a free-tier DID whose balance sits below FREE_MONTHLY_FLOOR is lifted TO
that number, never by it. An agent that spent nothing all month starts the next
one at 30, not at 60 — nothing accumulates, so an idle key cannot be parked for
a year and then cashed in.

Anonymous callers are untouched by both: no DID, no allowance, same behaviour as
before. Paid subscriptions never reach here; the payer bypass in the credit
middleware returns before this module is consulted.

`agents` is owned by `postgres` and cannot be ALTERed by the app role, so the
per-DID state lives in its own role-owned table (precedent: PR #237's
email_path_registrations).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FREE_CALLS_PER_HOUR = 60
FREE_MONTHLY_FLOOR = 30


async def ensure_free_tier_tables(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS free_tier_state (
            did                TEXT PRIMARY KEY,
            hour_window        TIMESTAMPTZ NOT NULL DEFAULT date_trunc('hour', now()),
            calls_this_hour    INTEGER     NOT NULL DEFAULT 0,
            last_floor_month   DATE,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_free_tier_state_hour ON free_tier_state (hour_window)"
    )


async def consume_free_call(conn, did: str) -> bool:
    """Spend one hourly free call for `did`. True if the call is covered.

    One statement, so two concurrent requests cannot both read 59 and both
    decide they fit. The window resets by comparison rather than by a sweep:
    a row whose hour_window is older than the current hour starts a new hour on
    the next call, which means an agent that goes quiet needs no cleanup.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO free_tier_state (did, hour_window, calls_this_hour)
        VALUES ($1, date_trunc('hour', now()), 1)
        ON CONFLICT (did) DO UPDATE SET
            hour_window = CASE
                WHEN free_tier_state.hour_window < date_trunc('hour', now())
                THEN date_trunc('hour', now())
                ELSE free_tier_state.hour_window
            END,
            calls_this_hour = CASE
                WHEN free_tier_state.hour_window < date_trunc('hour', now()) THEN 1
                ELSE free_tier_state.calls_this_hour + 1
            END,
            updated_at = now()
        RETURNING calls_this_hour
        """,
        did,
    )
    return bool(row) and row["calls_this_hour"] <= FREE_CALLS_PER_HOUR


async def apply_monthly_floor(conn, did: str) -> int | None:
    """Lift `did` to FREE_MONTHLY_FLOOR once per calendar month.

    Returns the amount added when a top-up happened, None otherwise. A balance
    already at or above the floor is left alone and still consumes the month's
    entitlement, so the floor cannot be saved up by staying rich.
    """
    claimed = await conn.fetchval(
        """
        INSERT INTO free_tier_state (did, last_floor_month)
        VALUES ($1, date_trunc('month', current_date)::date)
        ON CONFLICT (did) DO UPDATE SET
            last_floor_month = date_trunc('month', current_date)::date,
            updated_at = now()
        WHERE free_tier_state.last_floor_month IS DISTINCT FROM
              date_trunc('month', current_date)::date
        RETURNING did
        """,
        did,
    )
    if not claimed:
        return None

    # A DID that has never been charged has no balance row yet; without this the
    # UPDATE below matches nothing and the month's entitlement is burnt for
    # nothing.
    from app.credits import ensure_balance_row
    await ensure_balance_row(conn, did)

    # The old balance has to come from a CTE: RETURNING sees the row after the
    # update, so `$2 - balance` there would always be zero.
    topped_up = await conn.fetchval(
        """
        WITH prev AS (
            SELECT balance FROM credit_balances WHERE did = $1 FOR UPDATE
        )
        UPDATE credit_balances cb
           SET balance = $2
          FROM prev
         WHERE cb.did = $1 AND prev.balance < $2
        RETURNING $2 - prev.balance
        """,
        did, FREE_MONTHLY_FLOOR,
    )
    return topped_up
