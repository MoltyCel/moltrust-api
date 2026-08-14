"""Tests for the AWS Marketplace fulfillment endpoint.

resolve_customer is mocked (no live AWS calls / no AWS_MP_* creds needed). The
sandbox DB table is created by the app startup hook (ensure_aws_marketplace_tables).

Since the subscription-notification consumer landed, ResolveCustomer no longer
grants access on its own: it records the buyer as pending and the welcome page
appears only after subscribe-success arrives on the queue.
"""
import pytest
from botocore.exceptions import ClientError

import app.aws_marketplace as awsmp

_ACCT = "111122223333"
_CUST = "X01EXAMPLECUSTOMER"


async def _cleanup():
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1",
            _ACCT,
        )
        await conn.execute(
            "DELETE FROM aws_marketplace_notifications WHERE customer_identifier = $1",
            _CUST,
        )


@pytest.mark.asyncio
async def test_fulfillment_resolves_and_persists_as_pending(async_client, monkeypatch):
    await _cleanup()
    fake = {
        "CustomerIdentifier": _CUST,
        "CustomerAWSAccountId": _ACCT,
        "ProductCode": awsmp.PRODUCT_CODE,
    }
    monkeypatch.setattr(awsmp, "_resolve_customer", lambda token: fake)

    resp = await async_client.post(
        "/aws/fulfillment", data={"x-amzn-marketplace-token": "tok-abc"}
    )
    assert resp.status_code == 200
    assert "Confirming your subscription" in resp.text
    assert awsmp.SUPPORT_EMAIL in resp.text
    # The gate: no signup CTA before AWS confirms the purchase.
    assert awsmp.SIGNUP_URL not in resp.text
    # Identifiers are never surfaced to the buyer.
    assert _CUST not in resp.text

    import app.main as m
    async with m.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1",
            _ACCT,
        )
    assert row is not None
    assert row["product_code"] == awsmp.PRODUCT_CODE
    assert row["subscription_status"] == awsmp.STATUS_PENDING
    # Persisted deliberately: the only join key the SNS notification carries.
    assert row["customer_identifier"] == _CUST
    assert row["license_arn"] is None          # absent from this mocked response
    assert row["moltrust_account_id"] is None  # nullable, linked later
    await _cleanup()


@pytest.mark.asyncio
async def test_welcome_page_only_after_subscribe_success(async_client, monkeypatch):
    await _cleanup()
    fake = {
        "CustomerIdentifier": _CUST,
        "CustomerAWSAccountId": _ACCT,
        "ProductCode": awsmp.PRODUCT_CODE,
    }
    monkeypatch.setattr(awsmp, "_resolve_customer", lambda token: fake)

    first = await async_client.post(
        "/aws/fulfillment", data={"x-amzn-marketplace-token": "tok-1"}
    )
    assert "Confirming your subscription" in first.text

    import app.main as m
    from datetime import datetime, timezone
    async with m.db_pool.acquire() as conn:
        await awsmp.apply_subscription_action(
            conn, "subscribe-success", _CUST, awsmp.PRODUCT_CODE,
            datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
        )

    second = await async_client.post(
        "/aws/fulfillment", data={"x-amzn-marketplace-token": "tok-2"}
    )
    assert second.status_code == 200
    assert "Welcome to MolTrust" in second.text
    assert awsmp.SIGNUP_URL in second.text
    assert f"aws_ref={_ACCT}" in second.text
    await _cleanup()


@pytest.mark.asyncio
async def test_revisit_does_not_reset_an_active_subscriber(async_client, monkeypatch):
    await _cleanup()
    fake = {
        "CustomerIdentifier": _CUST,
        "CustomerAWSAccountId": _ACCT,
        "ProductCode": awsmp.PRODUCT_CODE,
    }
    monkeypatch.setattr(awsmp, "_resolve_customer", lambda token: fake)
    await async_client.post("/aws/fulfillment", data={"x-amzn-marketplace-token": "t1"})

    import app.main as m
    from datetime import datetime, timezone
    async with m.db_pool.acquire() as conn:
        await awsmp.apply_subscription_action(
            conn, "subscribe-success", _CUST, awsmp.PRODUCT_CODE,
            datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
        )

    await async_client.post("/aws/fulfillment", data={"x-amzn-marketplace-token": "t2"})

    async with m.db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1",
            _ACCT,
        )
    assert status == awsmp.STATUS_ACTIVE
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
