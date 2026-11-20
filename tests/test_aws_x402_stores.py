"""Replay stores against a real Postgres (AWS-X402 v2, 20.11.2026).

Runs only with AWS_X402_TEST_DSN set to a throwaway database, e.g.
postgresql://test@127.0.0.1:55432/postgres. Skipped otherwise.
"""
import asyncio
import os
import time

import pytest

DSN = os.environ.get("AWS_X402_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="AWS_X402_TEST_DSN not set")


@pytest.fixture
def clean():
    import psycopg2
    conn = psycopg2.connect(DSN)
    with conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS aws_x402_payments, aws_x402_dpop_jti")
    conn.close()


async def test_payment_claimed_once_even_under_concurrency(clean):
    import asyncpg

    from app.aws_x402.stores import PgPaymentStore
    pool = await asyncpg.create_pool(DSN, min_size=2, max_size=10)
    try:
        a, b = PgPaymentStore(lambda: pool), PgPaymentStore(lambda: pool)  # two "instances"
        exp = int(time.time()) + 300
        await a.claim("warm", exp)  # create tables once, outside the race
        results = await asyncio.gather(*[(a if i % 2 else b).claim("awsmp-1", exp)
                                         for i in range(20)])
        assert results.count(True) == 1
        assert await b.claim("awsmp-2", exp) is True
    finally:
        await pool.close()


async def test_expired_payment_id_can_be_taken_over(clean):
    import asyncpg

    from app.aws_x402.stores import PgPaymentStore
    pool = await asyncpg.create_pool(DSN)
    try:
        s = PgPaymentStore(lambda: pool)
        assert await s.claim("awsmp-old", int(time.time()) - 1) is True
        assert await s.claim("awsmp-old", int(time.time()) + 300) is True
        assert await s.claim("awsmp-old", int(time.time()) + 300) is False
    finally:
        await pool.close()


async def test_no_pool_raises():
    from app.aws_x402.stores import PgPaymentStore
    with pytest.raises(RuntimeError):
        await PgPaymentStore(lambda: None).claim("x", 0)


def test_dpop_jti_first_use_passes_second_is_replay(clean):
    from app.aws_x402.stores import PgDPoPNonceStore
    s1, s2 = PgDPoPNonceStore(DSN), PgDPoPNonceStore(DSN)
    assert s1.has("jti-1") is False
    s1.add("jti-1", int(time.time()) + 60)
    assert s2.has("jti-1") is True
    assert s2.has("jti-2") is False


def test_dpop_store_unreachable_raises():
    import psycopg2

    from app.aws_x402.stores import PgDPoPNonceStore
    with pytest.raises(psycopg2.OperationalError):
        PgDPoPNonceStore("postgresql://nobody@127.0.0.1:1/none").has("jti")
