"""Tests for the AWS Marketplace fulfillment endpoint.

resolve_customer is mocked (no live AWS calls / no AWS_MP_* creds needed). The
sandbox DB table is created by the app startup hook (ensure_aws_marketplace_tables).
"""
import pytest
from botocore.exceptions import ClientError

import app.aws_marketplace as awsmp

_ACCT = "111122223333"


async def _cleanup():
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1",
            _ACCT,
        )


@pytest.mark.asyncio
async def test_fulfillment_resolves_and_persists(async_client, monkeypatch):
    await _cleanup()
    fake = {
        "CustomerIdentifier": "DEPRECATED_MUST_NOT_BE_USED",
        "CustomerAWSAccountId": _ACCT,
        "ProductCode": awsmp.PRODUCT_CODE,
    }
    monkeypatch.setattr(awsmp, "_resolve_customer", lambda token: fake)

    resp = await async_client.post(
        "/aws/fulfillment", data={"x-amzn-marketplace-token": "tok-abc"}
    )
    assert resp.status_code == 200
    assert "Welcome to MolTrust" in resp.text
    assert awsmp.SIGNUP_URL in resp.text
    assert awsmp.SUPPORT_EMAIL in resp.text
    # CustomerIdentifier (deprecated) must never be surfaced
    assert "DEPRECATED_MUST_NOT_BE_USED" not in resp.text

    import app.main as m
    async with m.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1",
            _ACCT,
        )
    assert row is not None
    assert row["product_code"] == awsmp.PRODUCT_CODE
    assert row["license_arn"] is None          # not returned by resolve_customer
    assert row["moltrust_account_id"] is None  # nullable, linked later
    await _cleanup()


@pytest.mark.asyncio
async def test_fulfillment_is_idempotent(async_client, monkeypatch):
    await _cleanup()
    fake = {"CustomerAWSAccountId": _ACCT, "ProductCode": awsmp.PRODUCT_CODE}
    monkeypatch.setattr(awsmp, "_resolve_customer", lambda token: fake)

    for _ in range(2):
        r = await async_client.post(
            "/aws/fulfillment", data={"x-amzn-marketplace-token": "tok-same"}
        )
        assert r.status_code == 200

    import app.main as m
    async with m.db_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1",
            _ACCT,
        )
    assert cnt == 1  # upsert, not a duplicate
    await _cleanup()


@pytest.mark.asyncio
async def test_expired_or_invalid_token_returns_400(async_client, monkeypatch):
    def boom(token):
        raise ClientError(
            {"Error": {"Code": "InvalidTokenException", "Message": "expired"}},
            "ResolveCustomer",
        )
    monkeypatch.setattr(awsmp, "_resolve_customer", boom)
    r = await async_client.post(
        "/aws/fulfillment", data={"x-amzn-marketplace-token": "bad"}
    )
    assert r.status_code == 400
    assert "4 hours" in r.text


@pytest.mark.asyncio
async def test_missing_token_returns_422(async_client):
    r = await async_client.post("/aws/fulfillment", data={})
    assert r.status_code == 422  # Form(...) is required
