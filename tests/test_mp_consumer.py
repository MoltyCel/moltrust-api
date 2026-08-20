"""Tests for the AWS Marketplace subscription notification consumer.

No live AWS: envelope parsing is pure, and the state transitions run against the
sandbox DB through the same helpers the consumer uses.

The message shape asserted here mirrors the AWS documentation, not a captured
live message — see the module docstring of agents/mp_subscription_consumer.py.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import app.aws_marketplace as awsmp
from agents import mp_subscription_consumer as consumer

_ACCT = "444455556666"
_CUST = "X01TESTCUSTOMER"
_T0 = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)


def _sns_message(action, msg_id="msg-1", timestamp="2026-08-15T10:00:00.000Z",
                 customer=_CUST, offer=None, free_trial=None):
    payload = {"action": action, "customer-identifier": customer,
               "product-code": awsmp.PRODUCT_CODE}
    if offer is not None:
        payload["offer-identifier"] = offer
    if free_trial is not None:
        payload["isFreeTrialTermPresent"] = free_trial
    envelope = {"Type": "Notification", "MessageId": msg_id,
                "TopicArn": "arn:aws:sns:us-east-1:287250355862:aws-mp-subscription-notification-x",
                "Message": json.dumps(payload), "Timestamp": timestamp}
    return {"MessageId": "sqs-" + msg_id, "ReceiptHandle": "rh-" + msg_id,
            "Body": json.dumps(envelope)}


async def _cleanup():
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1", _ACCT)
        await conn.execute(
            "DELETE FROM aws_marketplace_notifications WHERE customer_identifier = $1", _CUST)


async def _seed_subscriber(conn, license_arn=None):
    await conn.execute(
        """
        INSERT INTO aws_marketplace_subscribers
            (customer_aws_account_id, customer_identifier, license_arn, product_code)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (customer_aws_account_id, product_code, COALESCE(license_arn, ''))
        DO NOTHING
        """,
        _ACCT, _CUST, license_arn, awsmp.PRODUCT_CODE,
    )


# --- envelope parsing (no DB) -------------------------------------------------

def test_parse_envelope_unwraps_sns_notification():
    ev = consumer.parse_envelope(_sns_message("subscribe-success"))
    assert ev is not None
    assert ev["channel"] == "sns"
    assert ev["event_id"] == "msg-1"
    assert ev["action"] == "subscribe-success"
    assert ev["customer_identifier"] == _CUST
    assert ev["timestamp"] == _T0


def test_parse_envelope_handles_raw_delivery():
    raw = {"MessageId": "sqs-raw", "ReceiptHandle": "rh",
           "Body": json.dumps({"action": "subscribe-success",
                               "customer-identifier": _CUST,
                               "product-code": awsmp.PRODUCT_CODE})}
    ev = consumer.parse_envelope(raw)
    assert ev["event_id"] == "sqs-raw"   # falls back to the SQS id
    assert ev["action"] == "subscribe-success"
    assert ev["timestamp"] is None


def test_parse_envelope_rejects_non_json_body():
    assert consumer.parse_envelope({"MessageId": "x", "Body": "not json"}) is None


def test_free_trial_flag_is_a_string_not_a_bool():
    assert consumer._as_bool("true") is True
    assert consumer._as_bool("false") is False
    assert consumer._as_bool(None) is None


def test_all_documented_actions_are_mapped():
    assert set(awsmp.ACTION_STATUS) == {
        "subscribe-success", "subscribe-fail", "unsubscribe-pending", "unsubscribe-success"}


# --- state transitions (DB) ---------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_success_activates(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed_subscriber(conn)
        assert await consumer.handle_message(conn, _sns_message("subscribe-success"))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_ACTIVE
    await _cleanup()


@pytest.mark.asyncio
async def test_unsubscribe_success_revokes(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed_subscriber(conn)
        await consumer.handle_message(conn, _sns_message("subscribe-success", "m1"))
        await consumer.handle_message(
            conn, _sns_message("unsubscribe-success", "m2", "2026-08-15T11:00:00.000Z"))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_CANCELLED
    await _cleanup()


@pytest.mark.asyncio
async def test_duplicate_delivery_changes_nothing(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed_subscriber(conn)
        msg = _sns_message("subscribe-success", "dup-1")
        assert await consumer.handle_message(conn, msg)
        assert await consumer.handle_message(conn, msg)   # redelivered
        rows = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_notifications WHERE event_id = $1",
            "dup-1")
    assert rows == 1
    await _cleanup()


@pytest.mark.asyncio
async def test_stale_message_does_not_revive_a_cancelled_subscription(app_with_lifespan):
    """SQS is unordered: a late subscribe-success must not undo a cancellation."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed_subscriber(conn)
        await consumer.handle_message(
            conn, _sns_message("unsubscribe-success", "late-1", "2026-08-15T12:00:00.000Z"))
        await consumer.handle_message(
            conn, _sns_message("subscribe-success", "late-2", "2026-08-15T09:00:00.000Z"))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_CANCELLED
    await _cleanup()


@pytest.mark.asyncio
async def test_uncorrelated_message_is_kept_for_replay(app_with_lifespan):
    """A notification arriving before ResolveCustomer must not be dropped."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        assert await consumer.handle_message(conn, _sns_message("subscribe-success", "early-1"))
        matched = await conn.fetchval(
            "SELECT matched FROM aws_marketplace_notifications WHERE event_id = $1",
            "early-1")
        assert matched is False

        # ResolveCustomer lands afterwards and replays the pending event.
        status = await awsmp._persist_subscriber(
            conn, _ACCT, _CUST, None, awsmp.PRODUCT_CODE)
        assert status == awsmp.STATUS_ACTIVE
        matched = await conn.fetchval(
            "SELECT matched FROM aws_marketplace_notifications WHERE event_id = $1",
            "early-1")
    assert matched is True
    await _cleanup()


@pytest.mark.asyncio
async def test_unknown_action_is_recorded_without_state_change(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed_subscriber(conn)
        assert await consumer.handle_message(conn, _sns_message("entitlement-updated", "unk-1"))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
        recorded = await conn.fetchval(
            "SELECT action FROM aws_marketplace_notifications WHERE event_id = $1",
            "unk-1")
    assert status == awsmp.STATUS_PENDING   # untouched
    assert recorded == "entitlement-updated"
    await _cleanup()


@pytest.mark.asyncio
async def test_concurrent_agreements_all_move_together(app_with_lifespan):
    """Two agreements on one account: SNS carries no agreement id, so both move."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed_subscriber(conn, license_arn="arn:aws:license-manager:us-east-1:1:license/l-1")
        await _seed_subscriber(conn, license_arn="arn:aws:license-manager:us-east-1:1:license/l-2")
        count = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1",
            _ACCT)
        assert count == 2   # the old (account, product) primary key would have blocked this

        await consumer.handle_message(conn, _sns_message("subscribe-success", "multi-1"))
        active = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1 AND subscription_status = $2",
            _ACCT, awsmp.STATUS_ACTIVE)
        matched_rows = await conn.fetchval(
            "SELECT matched_rows FROM aws_marketplace_notifications WHERE event_id = $1",
            "multi-1")
    assert active == 2
    assert matched_rows == 2
    await _cleanup()
