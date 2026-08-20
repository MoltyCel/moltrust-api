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
_LICENSE = "arn:aws:license-manager::294406891311:license:l-testfixture0001"


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
async def test_signup_is_offered_immediately_without_any_notification(async_client, monkeypatch):
    """AWS requires access straight after subscribing — see PR notes.

    This assertion is the inverse of the one it replaces. Holding the CTA until
    a notification arrived is what produced the dead end the 2026-08-19 review
    flagged, and no notification has ever reached this listing.
    """
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
    assert "Welcome to MolTrust" in resp.text
    assert awsmp.SIGNUP_URL in resp.text          # no notification was needed
    assert f"aws_ref={_ACCT}" in resp.text
    assert awsmp.SUPPORT_EMAIL in resp.text
    # Identifiers are never surfaced to the buyer.
    assert _CUST not in resp.text
    # And no page offers a GET back to this POST-only route (the 405 source).
    assert 'href=""' not in resp.text

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
async def test_status_stays_bookkeeping_until_a_license_event(async_client, monkeypatch):
    """pending vs active is recorded, but it never blocks the signup."""
    await _cleanup()
    fake = {
        "CustomerIdentifier": _CUST,
        "CustomerAWSAccountId": _ACCT,
        "ProductCode": awsmp.PRODUCT_CODE,
        "LicenseArn": _LICENSE,
    }
    monkeypatch.setattr(awsmp, "_resolve_customer", lambda token: fake)

    first = await async_client.post(
        "/aws/fulfillment", data={"x-amzn-marketplace-token": "tok-1"}
    )
    assert awsmp.SIGNUP_URL in first.text

    import app.main as m
    async with m.db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_PENDING      # recorded, not enforced

    from datetime import datetime, timezone
    async with m.db_pool.acquire() as conn:
        # An account id alone no longer correlates: one account can hold
        # several concurrent agreements, and matching on it would move all of
        # them. The event has to name a licence or an agreement.
        none_matched, _ = await awsmp.apply_license_event(
            conn, "License Updated - Manufacturer", _ACCT, None,
            awsmp.PRODUCT_CODE, datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc))
        assert none_matched == 0

        matched, updated = await awsmp.apply_license_event(
            conn, "License Updated - Manufacturer", _ACCT, _LICENSE,
            awsmp.PRODUCT_CODE, datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc),
            "agmt-testagreement0001")
        assert matched == 1 and updated == 1
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_ACTIVE
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
