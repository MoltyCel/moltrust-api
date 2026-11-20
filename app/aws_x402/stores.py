"""Replay protection shared by every API instance.

Two records, both in Postgres so that any instance sees what another accepted:

- aws_x402_payments: each AWS payment_id is accepted once, until the later of
  the payment token's and its quote's expiry plus skew. AWS: "Accept each
  payment token for only the one request that its quote priced. Keep a record
  of each accepted token until the token expires" (agentic-commerce-verify-
  payment-tokens.html, read 20.11.2026).
- aws_x402_dpop_jti: each DPoP proof id once (RFC 9449 §11.1), as the seller
  SDK's DPoPNonceStore protocol asks.

Both claims are a single atomic statement: insert, or take over a row whose
expiry has passed; zero rows back means the id is live and the request is a
replay. A database error refuses the payment: the stores never answer "not
seen" when they could not look.

The SDK calls the DPoP store synchronously, so that store uses psycopg2 with
one short connection per call. The payment store is async (asyncpg pool).
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS aws_x402_payments (
    payment_id  text PRIMARY KEY,
    expires_at  timestamptz NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS aws_x402_dpop_jti (
    jti         text PRIMARY KEY,
    expires_at  timestamptz NOT NULL
);
"""

_CLAIM_PAYMENT = """
INSERT INTO aws_x402_payments (payment_id, expires_at)
VALUES ($1, to_timestamp($2))
ON CONFLICT (payment_id) DO UPDATE
    SET expires_at = EXCLUDED.expires_at, accepted_at = now()
    WHERE aws_x402_payments.expires_at <= now()
RETURNING payment_id
"""

_CLAIM_JTI = """
INSERT INTO aws_x402_dpop_jti (jti, expires_at)
VALUES (%s, to_timestamp(%s))
ON CONFLICT (jti) DO UPDATE
    SET expires_at = EXCLUDED.expires_at
    WHERE aws_x402_dpop_jti.expires_at <= now()
RETURNING jti
"""

_EXTEND_JTI = "UPDATE aws_x402_dpop_jti SET expires_at = to_timestamp(%s) WHERE jti = %s"

# A store that records the id inside has() must hold it on its own for about
# 70 s, because add() is not called when the payment is refused after has()
# (seller SDK, python.md "DPoP replay store"). DPoP max age is 60 s.
_JTI_HOLD_SECONDS = 70


class PgPaymentStore:
    """Atomic claim of a payment_id across instances (asyncpg)."""

    def __init__(self, get_pool: Callable[[], Any]):
        self._get_pool = get_pool
        self._ready = False

    async def _pool(self):
        pool = self._get_pool()
        if pool is None:
            raise RuntimeError("database pool not available")
        if not self._ready:
            async with pool.acquire() as conn:
                await conn.execute(DDL)
            self._ready = True
        return pool

    async def claim(self, payment_id: str, expires_at: int) -> bool:
        """True if this call recorded the id; False if it is live already."""
        pool = await self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_CLAIM_PAYMENT, payment_id, expires_at)
        return row is not None


class PgDPoPNonceStore:
    """fiat_extension_x402 DPoPNonceStore on Postgres (psycopg2, synchronous).

    has() is the atomic check-and-set; add() only extends the expiry.
    """

    def __init__(self, dsn: str, connect: Callable[..., Any] | None = None):
        self._dsn = dsn
        if connect is None:
            import psycopg2
            connect = psycopg2.connect
        self._connect = connect
        self._ready = False

    def _run(self, sql: str, args: tuple) -> Any:
        conn = self._connect(self._dsn, connect_timeout=3)
        try:
            with conn, conn.cursor() as cur:
                if not self._ready:
                    cur.execute(DDL)
                    self._ready = True
                cur.execute(sql, args)
                return cur.fetchone() if cur.description else None
        finally:
            conn.close()

    def has(self, jti: str) -> bool:
        row = self._run(_CLAIM_JTI, (jti, int(time.time()) + _JTI_HOLD_SECONDS))
        return row is None  # no row back: the id is live, a replay

    def add(self, jti: str, expires_at: int) -> None:
        self._run(_EXTEND_JTI, (int(expires_at), jti))


class MemoryPaymentStore:
    """Single process only. Tests and local runs."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    async def claim(self, payment_id: str, expires_at: int) -> bool:
        now = time.time()
        self._seen = {k: v for k, v in self._seen.items() if v > now}
        if payment_id in self._seen:
            return False
        self._seen[payment_id] = expires_at
        return True


class MemoryNonceStore:
    """Single process only. Tests and local runs."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def has(self, jti: str) -> bool:
        exp = self._seen.get(jti)
        return exp is not None and time.time() < exp

    def add(self, jti: str, expires_at: int) -> None:
        now = time.time()
        self._seen = {k: v for k, v in self._seen.items() if v > now}
        self._seen[jti] = expires_at


PaymentStore = Callable[[str, int], Awaitable[bool]]
