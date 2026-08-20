"""EventBridge onboarding: envelope, correlation, gate relocation, dedup.

The envelope asserted here comes from the AWS seller guide ("Amazon EventBridge
events"), NOT from an event captured from this listing — none has ever arrived.
The consumer is built so that an unrecognised shape is stored rather than
dropped, and one test pins that behaviour, because the first real delivery is
what will confirm or correct everything else in this file.
"""
import json
from datetime import datetime, timezone

import pytest

import app.aws_marketplace as awsmp
from agents import mp_subscription_consumer as consumer

_ACCT = "555566667777"
_LIC = "arn:aws:license-manager::294406891311:license:l-51b9c48f8e8847cb9d1c8f4708155afc"
_T = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _event(detail_type, event_id="ev-1", time="2026-08-20T12:00:00Z",
           license_arn=None, account=_ACCT):
    detail = {
        "requestId": "3d4c9f9b-b809-4f5e-9fac-a9ae98b05cbb",
        "catalog": "AWSMarketplace",
        "agreement": {"id": "agmt-bnggcpns1fwir6ewft30vf6s2"},
        "product": {"code": awsmp.PRODUCT_CODE, "id": "prod-k35yywhyj4eqg"},
        "acceptor": {"accountId": account},
        "proposer": {"accountId": "993604559884"},
        "offer": {"id": "offer-1234567890123"},
    }
    if license_arn:
        detail["license"] = {"arn": license_arn}
    body = {
        "version": "0", "id": event_id, "detail-type": detail_type,
        "source": "aws.agreement-marketplace", "account": "993604559884",
        "time": time, "region": "us-east-1",
        "resources": ["arn:aws:aws-marketplace::aws:agreement:agmt-bnggcpns1fwir6ewft30vf6s2"],
        "detail": detail,
    }
    return {"MessageId": "sqs-" + event_id, "ReceiptHandle": "rh", "Body": json.dumps(body)}


async def _cleanup():
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM aws_marketplace_subscribers WHERE customer_aws_account_id = $1", _ACCT)
        await conn.execute(
            "DELETE FROM aws_marketplace_notifications WHERE acceptor_account_id = $1", _ACCT)


async def _seed(conn, license_arn=None):
    await conn.execute(
        "INSERT INTO aws_marketplace_subscribers "
        "(customer_aws_account_id, license_arn, product_code) VALUES ($1, $2, $3) "
        "ON CONFLICT (customer_aws_account_id, product_code, COALESCE(license_arn, '')) "
        "DO NOTHING",
        _ACCT, license_arn, awsmp.PRODUCT_CODE)


# --- envelope (no DB) --------------------------------------------------------

def test_eventbridge_envelope_is_recognised_and_unpacked():
    ev = consumer.parse_envelope(_event("License Updated - Manufacturer", license_arn=_LIC))
    assert ev["channel"] == "eventbridge"
    assert ev["event_id"] == "ev-1"
    assert ev["detail_type"] == "License Updated - Manufacturer"
    assert ev["event_source"] == "aws.agreement-marketplace"
    assert ev["acceptor_account_id"] == _ACCT
    assert ev["license_arn"] == _LIC
    assert ev["agreement_id"] == "agmt-bnggcpns1fwir6ewft30vf6s2"
    assert ev["product_code"] == awsmp.PRODUCT_CODE
    assert ev["timestamp"] == _T


def test_sns_envelope_still_recognised_on_the_same_queue():
    """The SNS subscription stays wired, so one consumer must read both."""
    inner = {"action": "subscribe-success", "customer-identifier": "X01",
             "product-code": awsmp.PRODUCT_CODE}
    body = {"Type": "Notification", "MessageId": "sns-1",
            "Message": json.dumps(inner), "Timestamp": "2026-08-20T12:00:00Z"}
    ev = consumer.parse_envelope({"MessageId": "sqs-x", "Body": json.dumps(body)})
    assert ev["channel"] == "sns"
    assert ev["action"] == "subscribe-success"


def test_role_suffix_is_ignored_when_mapping_detail_type():
    for suffix in ("- Manufacturer", "- Proposer"):
        assert awsmp.event_status("License Updated " + suffix) == awsmp.STATUS_ACTIVE
    assert awsmp.event_status("License Deprovisioned - Manufacturer") == awsmp.STATUS_CANCELLED
    assert awsmp.event_status("Purchase Agreement Ended - Proposer") == awsmp.STATUS_CANCELLED
    assert awsmp.event_status("Purchase Agreement Created - Manufacturer") == awsmp.STATUS_PENDING
    assert awsmp.event_status("Offer Released") is None


# --- correlation + state (DB) -------------------------------------------------

@pytest.mark.asyncio
async def test_license_updated_activates(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed(conn, _LIC)
        assert await consumer.handle_message(conn, _event("License Updated - Manufacturer",
                                                          license_arn=_LIC))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_ACTIVE
    await _cleanup()


@pytest.mark.asyncio
async def test_purchase_agreement_created_does_not_activate(app_with_lifespan):
    """Step 9 of the onboarding guide: only License Updated activates."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed(conn)
        await consumer.handle_message(conn, _event("Purchase Agreement Created - Manufacturer",
                                                   event_id="ev-created"))
        status = await conn.fetchval(
            "SELECT subscription_status FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert status == awsmp.STATUS_PENDING
    await _cleanup()


@pytest.mark.asyncio
async def test_deprovision_and_agreement_end_revoke(app_with_lifespan):
    for i, dt in enumerate(("License Deprovisioned - Manufacturer",
                            "Purchase Agreement Ended - Manufacturer")):
        await _cleanup()
        import app.main as m
        async with m.db_pool.acquire() as conn:
            await _seed(conn, _LIC)
            await consumer.handle_message(conn, _event("License Updated - Manufacturer",
                                                       event_id=f"up-{i}", license_arn=_LIC))
            await consumer.handle_message(conn, _event(
                dt, event_id=f"rev-{i}", time="2026-08-20T13:00:00Z", license_arn=_LIC))
            status = await conn.fetchval(
                "SELECT subscription_status FROM aws_marketplace_subscribers "
                "WHERE customer_aws_account_id = $1", _ACCT)
        assert status == awsmp.STATUS_CANCELLED, dt
    await _cleanup()


@pytest.mark.asyncio
async def test_correlation_uses_account_not_exact_arn_string(app_with_lifespan):
    """The documented ARN form differs from the one ResolveCustomer returns."""
    await _cleanup()
    import app.main as m
    doc_form = "aws:license-manager:us-east-1:294406891311:l-51b9c48f8e8847cb9d1c8f4708155afc"
    async with m.db_pool.acquire() as conn:
        await _seed(conn, _LIC)                      # stored in the ResolveCustomer form
        matched, updated = await awsmp.apply_license_event(
            conn, "License Updated - Manufacturer", _ACCT, doc_form,
            awsmp.PRODUCT_CODE, _T)
    assert matched == 1 and updated == 1
    await _cleanup()


@pytest.mark.asyncio
async def test_unknown_detail_type_is_stored_not_dropped(app_with_lifespan):
    """The real envelope is unverified — nothing may be discarded as noise."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed(conn)
        assert await consumer.handle_message(conn, _event("Some Future Event - Manufacturer",
                                                          event_id="ev-unknown"))
        row = await conn.fetchrow(
            "SELECT detail_type, channel, matched, raw FROM aws_marketplace_notifications "
            "WHERE event_id = $1", "ev-unknown")
    assert row is not None
    assert row["detail_type"] == "Some Future Event - Manufacturer"
    assert row["channel"] == "eventbridge"
    assert row["matched"] is False
    assert json.loads(row["raw"])["detail"]["acceptor"]["accountId"] == _ACCT
    await _cleanup()


@pytest.mark.asyncio
async def test_duplicate_event_id_changes_nothing(app_with_lifespan):
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await _seed(conn, _LIC)
        msg = _event("License Updated - Manufacturer", event_id="dup-1", license_arn=_LIC)
        assert await consumer.handle_message(conn, msg)
        assert await consumer.handle_message(conn, msg)
        n = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_notifications WHERE event_id = $1", "dup-1")
    assert n == 1
    await _cleanup()


# --- dedup -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_then_present_license_arn_yields_one_row(app_with_lifespan):
    """The 2026-08-19 purchase created two rows for one agreement."""
    await _cleanup()
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await awsmp._persist_subscriber(conn, _ACCT, None, None, awsmp.PRODUCT_CODE)
        await awsmp._persist_subscriber(conn, _ACCT, None, _LIC, awsmp.PRODUCT_CODE)
        rows = await conn.fetch(
            "SELECT license_arn FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert len(rows) == 1, "the ARN-less row must be filled, not duplicated"
    assert rows[0]["license_arn"] == _LIC
    await _cleanup()


@pytest.mark.asyncio
async def test_two_real_agreements_still_get_two_rows(app_with_lifespan):
    """Dedup must not collapse genuine concurrent agreements."""
    await _cleanup()
    other = _LIC[:-4] + "beef"
    import app.main as m
    async with m.db_pool.acquire() as conn:
        await awsmp._persist_subscriber(conn, _ACCT, None, _LIC, awsmp.PRODUCT_CODE)
        await awsmp._persist_subscriber(conn, _ACCT, None, other, awsmp.PRODUCT_CODE)
        n = await conn.fetchval(
            "SELECT count(*) FROM aws_marketplace_subscribers "
            "WHERE customer_aws_account_id = $1", _ACCT)
    assert n == 2
    await _cleanup()
