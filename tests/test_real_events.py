"""Correlation against the four EventBridge events this listing actually sent.

These payloads are verbatim from aws_marketplace_notifications, captured
2026-08-20 14:23–14:25 UTC (agreement agmt-2nehijlqfz9g7f2v6imgyjhmu, buyer
782858285006). Everything before this was built against documentation; this is
the first test suite written against what AWS really emits.

What the real events showed, and what these tests pin:
  * the agreement events carry NO product and NO licence ARN
  * only License Updated / Deprovisioned carry both
  * detail.agreement.id is on all four — the one stable correlator
  * the agreement is created BEFORE the buyer reaches the registration page
    (License Updated landed 21s ahead of the fulfillment POST)
"""
import json

import pytest

import app.aws_marketplace as awsmp
from agents import mp_subscription_consumer as consumer

_ACCT = "782858285006"
_AGMT = "agmt-2nehijlqfz9g7f2v6imgyjhmu"
_LIC = "arn:aws:license-manager::294406891311:license:l-9d01d15f86334a8894365cd7350556ff"
_PROD = "74az0btybm649octamy0sktos"

_CREATED = {
    "id": "84a4f48b-44a8-0b77-c2a7-6448b8e6de3d", "time": "2026-08-20T14:23:22Z",
    "version": "0", "region": "us-east-1", "account": "993604559884",
    "source": "aws.agreement-marketplace",
    "detail-type": "Purchase Agreement Created - Proposer",
    "resources": ["arn:aws:aws-marketplace::aws:agreement:" + _AGMT],
    "detail": {
        "offer": {"id": "offer-uovnpk2f6jq7s"}, "catalog": "AWSMarketplace",
        "acceptor": {"accountId": _ACCT}, "proposer": {"accountId": "993604559884"},
        "agreement": {"id": _AGMT, "intent": "NEW", "status": "ACTIVE",
                      "endTime": "9999-01-01T00:00:00Z",
                      "startTime": "2026-08-20T14:23:11.013Z",
                      "acceptanceTime": "2026-08-20T14:23:11.013Z"},
        "requestId": "40d82a8d-d914-56b8-a8b3-1caf788fc30f",
        "resaleAuthorization": {"id": None},
    },
}
_UPDATED = {
    "id": "6c53798d-327e-b2e5-c20e-52fec89bc174", "time": "2026-08-20T14:24:23Z",
    "version": "0", "region": "us-east-1", "account": "993604559884",
    "source": "aws.agreement-marketplace",
    "detail-type": "License Updated - Manufacturer",
    "resources": ["arn:aws:aws-marketplace::aws:agreement:" + _AGMT],
    "detail": {
        "offer": {"id": "offer-uovnpk2f6jq7s"}, "catalog": "AWSMarketplace",
        "license": {"arn": _LIC},
        "product": {"id": "prod-k35yywhyj4eqg", "code": _PROD},
        "acceptor": {"accountId": _ACCT}, "proposer": {"accountId": "993604559884"},
        "agreement": {"id": _AGMT},
        "requestId": "9838714e-d760-5508-8269-4738858dce43",
    },
}
_ENDED = {
    "id": "24befb37-189e-d64f-d474-4c1d6787fc8c", "time": "2026-08-20T14:24:57Z",
    "version": "0", "region": "us-east-1", "account": "993604559884",
    "source": "aws.agreement-marketplace",
    "detail-type": "Purchase Agreement Ended - Proposer",
    "resources": ["arn:aws:aws-marketplace::aws:agreement:" + _AGMT],
    "detail": {
        "catalog": "AWSMarketplace", "acceptor": {"accountId": _ACCT},
        "proposer": {"accountId": "993604559884"},
        "agreement": {"id": _AGMT, "status": "CANCELLED"},
        "requestId": "90990a7c-bfd9-514b-8fc7-3f028ebaf86b",
        "resaleAuthorization": {"id": None},
    },
}
_DEPROV = {
    "id": "7d02ce32-e523-b155-8b1a-b49abce1a7ff", "time": "2026-08-20T14:25:28Z",
    "version": "0", "region": "us-east-1", "account": "993604559884",
    "source": "aws.agreement-marketplace",
    "detail-type": "License Deprovisioned - Manufacturer",
    "resources": ["arn:aws:aws-marketplace::aws:agreement:" + _AGMT],
    "detail": {
        "offer": {"id": "offer-uovnpk2f6jq7s"}, "catalog": "AWSMarketplace",
        "license": {"arn": _LIC},
        "product": {"id": "prod-k35yywhyj4eqg", "code": _PROD},
        "acceptor": {"accountId": _ACCT}, "proposer": {"accountId": "993604559884"},
        "agreement": {"id": _AGMT},
        "requestId": "7cdcf098-1774-5ae7-9686-8a5878e84344",
    },
}


def _msg(event):
    return {"MessageId": "sqs-" + event["id"], "ReceiptHandle": "rh",
            "Body": json.dumps(event)}


async def _cleanup():
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1", _ACCT)
        await conn.execute(
            "DELETE FROM aws_marketplace_notifications WHERE acceptor_account_id = $1", _ACCT)


async def _register(conn, license_arn=_LIC):
    """What the fulfillment POST does: ResolveCustomer gives account + ARN."""
    return await awsmp._persist_subscriber(conn, _ACCT, None, license_arn, _PROD)


def test_real_agreement_events_carry_no_product_or_licence():
    """The assumption the old query got wrong, pinned against the real payload."""
    for ev in (_CREATED, _ENDED):
        parsed = consumer.parse_envelope(_msg(ev))
        assert parsed["agreement_id"] == _AGMT
        assert parsed["acceptor_account_id"] == _ACCT
        assert parsed["product_code"] is None
        assert parsed["license_arn"] is None
    for ev in (_UPDATED, _DEPROV):
        parsed = consumer.parse_envelope(_msg(ev))
        assert parsed["license_arn"] == _LIC
        assert parsed["product_code"] == _PROD
        assert parsed["agreement_id"] == _AGMT


@pytest.mark.asyncio
async def test_license_updated_activates_and_stamps_the_agreement(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _register(conn)
        assert await consumer.handle_message(conn, _msg(_UPDATED))
        row = await conn.fetchrow(
            "SELECT subscription_status, agreement_id FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert row["subscription_status"] == awsmp.STATUS_ACTIVE
    assert row["agreement_id"] == _AGMT, "the licence event must stamp the agreement"
    await _cleanup()


@pytest.mark.asyncio
async def test_agreement_ended_correlates_once_the_agreement_is_known(app_with_lifespan):
    """Ended carries no licence and no product — it can only match on the id."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _register(conn)
        await consumer.handle_message(conn, _msg(_UPDATED))
        assert await consumer.handle_message(conn, _msg(_ENDED))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
        matched = await conn.fetchval(
            "SELECT matched FROM aws_marketplace_notifications WHERE event_id = $1",
            _ENDED["id"])
    assert status == awsmp.STATUS_CANCELLED
    assert matched is True, "Ended stayed uncorrelated — this is the old bug"
    await _cleanup()


@pytest.mark.asyncio
async def test_deprovision_revokes(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _register(conn)
        await consumer.handle_message(conn, _msg(_UPDATED))
        await consumer.handle_message(conn, _msg(_DEPROV))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_CANCELLED
    await _cleanup()


@pytest.mark.asyncio
async def test_events_arriving_before_registration_are_replayed(app_with_lifespan):
    """The real order: agreement first, buyer 21 seconds later."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        for ev in (_CREATED, _UPDATED):
            await consumer.handle_message(conn, _msg(ev))
        unmatched = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_notifications "
            "WHERE acceptor_account_id = $1 AND NOT matched", _ACCT)
        assert unmatched == 2, "nothing to replay — precondition wrong"

        status = await _register(conn)          # the fulfillment POST

        assert status == awsmp.STATUS_ACTIVE, "replay did not activate the subscriber"
        still_unmatched = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_notifications "
            "WHERE acceptor_account_id = $1 AND NOT matched", _ACCT)
    assert still_unmatched == 1, "only Created stays open (no licence to match on)"
    await _cleanup()


@pytest.mark.asyncio
async def test_a_stale_arnless_row_is_not_repurposed(app_with_lifespan):
    """The 2026-08-19 row must not absorb a 2026-08-20 licence."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _register(conn, license_arn=None)          # first call, no ARN
        await conn.execute(
            "UPDATE aws_marketplace_subscribers SET created_at = NOW() - INTERVAL '34 hours' "
            "WHERE customer_aws_account_id = $1", _ACCT)
        await _register(conn, license_arn=_LIC)          # a later, different purchase
        rows = await conn.fetch(
            "SELECT license_arn FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1 ORDER BY id", _ACCT)
    assert len(rows) == 2, "the stale row was repurposed instead of leaving a new one"
    assert rows[0]["license_arn"] is None
    assert rows[1]["license_arn"] == _LIC
    await _cleanup()


@pytest.mark.asyncio
async def test_same_session_duplicate_is_still_collapsed(app_with_lifespan):
    """The genuine duplicate — two calls, one registration — stays one row."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _register(conn, license_arn=None)
        await _register(conn, license_arn=_LIC)
        n = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert n == 1
    await _cleanup()
