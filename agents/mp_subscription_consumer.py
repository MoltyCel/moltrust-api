#!/usr/bin/env python3
"""AWS Marketplace subscription notification consumer (SNS -> SQS -> Postgres).

Long-polls the seller-account SQS queue that is subscribed to the product's
aws-mp-subscription-notification SNS topic, and drives the subscription
lifecycle in aws_marketplace_subscribers. Without this, cancellations never
reach MolTrust and access is never revoked.

Run as a service (ops/systemd/moltrust-mp-consumer.service), not from cron: the
queue is long-polled continuously.

Message shape
-------------
An SQS message body is the SNS envelope; its `Message` field is the Marketplace
payload as a JSON *string* and has to be parsed a second time:

    {"Type": "Notification", "MessageId": "...", "Timestamp": "...",
     "Message": "{\\"action\\": \\"subscribe-success\\", ...}"}

The inner payload carries action, customer-identifier, product-code, and
optionally offer-identifier and isFreeTrialTermPresent.

IMPORTANT: those field names and the action values come from the AWS
documentation ("Amazon SNS notifications for SaaS products"). They are NOT
verified against a live message — the queue was empty when this was written.
The first real notification either confirms them or lands as an unknown action,
which is logged loudly rather than silently dropped.

Delivery semantics
------------------
SQS is at-least-once and unordered, so two guards apply:
  * idempotency — aws_marketplace_notifications has the SNS MessageId as its
    primary key; a repeat delivery inserts nothing and changes no state.
  * ordering — a transition is applied only if its SNS timestamp is newer than
    the state it would overwrite (see apply_subscription_action).

DeleteMessage runs only after the transaction commits. A crash in between means
the message reappears and is recognised as a duplicate.
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import asyncpg
import boto3
from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.aws_marketplace import (  # noqa: E402
    ACTION_STATUS,
    AWS_MP_REGION,
    apply_license_event,
    apply_subscription_action,
    event_status,
)

QUEUE_URL = os.environ.get(
    "AWS_MP_QUEUE_URL",
    "https://sqs.us-east-1.amazonaws.com/993604559884/moltrust-mp-subscription",
)
DB_USER = os.environ.get("DB_USER", "moltstack")
DB_NAME = os.environ.get("DB_NAME", "moltstack")

WAIT_SECONDS = 20   # SQS long poll; the maximum AWS allows
BATCH_SIZE = 10     # maximum ReceiveMessage returns per call
ERROR_BACKOFF = 30  # seconds to wait after an AWS or DB error before retrying

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("mp_consumer")


def _sqs_client():
    """boto3 SQS client on the SELLER account keys, mirroring _marketplace_client()."""
    ak = os.environ.get("AWS_MP_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_MP_SECRET_ACCESS_KEY")
    if not ak or not sk:
        raise RuntimeError("AWS_MP credentials not configured")
    return boto3.client(
        "sqs",
        region_name=AWS_MP_REGION,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    )


def _parse_timestamp(value):
    """SNS timestamps are ISO-8601 with a trailing Z; returns None if unusable."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        log.warning("unparseable SNS timestamp %r", value)
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _as_bool(value):
    """isFreeTrialTermPresent is delivered as a string, not a JSON boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return None


def parse_eventbridge(body, sqs_id):
    """Unwrap an EventBridge event delivered straight to SQS.

    EventBridge writes the event as the message body with no wrapper, so the
    shape is the envelope itself: id, detail-type, source, time, detail.

    NOTE: this layout comes from the AWS seller guide, not from a captured
    event for THIS listing — no EventBridge event has ever reached us. Nothing
    here rejects an unexpected shape: the raw event is always stored, so the
    first real delivery confirms or corrects the mapping without data loss.
    """
    detail = body.get("detail") or {}
    if not isinstance(detail, dict):
        detail = {}

    def dig(*path):
        cur = detail
        for key in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        return cur

    return {
        "channel": "eventbridge",
        "event_id": body.get("id") or sqs_id,
        "detail_type": body.get("detail-type"),
        "event_source": body.get("source"),
        "timestamp": _parse_timestamp(body.get("time")),
        "agreement_id": dig("agreement", "id"),
        "license_arn": dig("license", "arn"),
        "acceptor_account_id": dig("acceptor", "accountId"),
        "product_code": dig("product", "code"),
        "raw": body,
    }


def parse_sns(body, sqs_id):
    """Unwrap the SNS envelope whose Message field is the payload as a string."""
    try:
        payload = json.loads(body["Message"])
    except (TypeError, ValueError):
        log.error("SNS envelope %s carries a non-JSON Message", body.get("MessageId"))
        return None
    return {
        "channel": "sns",
        "event_id": body.get("MessageId") or sqs_id,
        "action": payload.get("action"),
        "customer_identifier": payload.get("customer-identifier"),
        "product_code": payload.get("product-code"),
        "offer_identifier": payload.get("offer-identifier"),
        "is_free_trial": _as_bool(payload.get("isFreeTrialTermPresent")),
        "timestamp": _parse_timestamp(body.get("Timestamp")),
        "raw": payload,
    }


def parse_envelope(message):
    """Unwrap one SQS message into the flat dict the handler works with.

    Handles both the standard SNS envelope and raw message delivery, in case the
    subscription is ever switched to raw. Returns None if the body is not JSON.
    """
    sqs_id = message.get("MessageId")
    try:
        body = json.loads(message.get("Body") or "")
    except (TypeError, ValueError):
        log.error("SQS message %s has a non-JSON body", sqs_id)
        return None
    if not isinstance(body, dict):
        log.error("SQS message %s is JSON but not an object", sqs_id)
        return None

    # One queue, two producers. The SNS subscription stays wired until
    # EventBridge is proven to deliver, so both shapes have to be recognised
    # here — a second poller on the same queue would race this one for
    # messages rather than complement it.
    if body.get("Type") == "Notification" and "Message" in body:
        return parse_sns(body, sqs_id)
    if "detail-type" in body and "detail" in body:
        return parse_eventbridge(body, sqs_id)

    # Raw SNS delivery: the body is the marketplace payload itself.
    if "action" in body:
        return {"channel": "sns", "event_id": sqs_id, "action": body.get("action"),
                "customer_identifier": body.get("customer-identifier"),
                "product_code": body.get("product-code"),
                "offer_identifier": body.get("offer-identifier"),
                "is_free_trial": _as_bool(body.get("isFreeTrialTermPresent")),
                "timestamp": None, "raw": body}

    log.warning("SQS message %s matches no known envelope — stored unparsed", sqs_id)
    return {"channel": "unknown", "event_id": sqs_id, "timestamp": None, "raw": body}


async def handle_message(conn, message):
    """Record and apply one message. Returns True if it may be deleted.

    Every message is written to aws_marketplace_notifications before anything
    is interpreted — including one whose envelope or detail-type we do not
    recognise. The EventBridge layout here is taken from documentation and has
    never been seen from this listing, so an unrecognised event has to survive
    as evidence rather than be dropped as noise.
    """
    ev = parse_envelope(message)
    if ev is None:
        # Malformed and unfixable by retrying — leave it for the queue's redrive
        # policy rather than deleting evidence.
        return False

    channel = ev.get("channel")
    event_id = ev.get("event_id")

    async with conn.transaction():
        inserted = await conn.fetchval(
            """
            INSERT INTO aws_marketplace_notifications
                (event_id, channel, action, detail_type, event_source,
                 customer_identifier, acceptor_account_id, agreement_id,
                 license_arn, product_code, offer_identifier, is_free_trial,
                 sns_timestamp, raw)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            event_id, channel, ev.get("action"), ev.get("detail_type"),
            ev.get("event_source"), ev.get("customer_identifier"),
            ev.get("acceptor_account_id"), ev.get("agreement_id"),
            ev.get("license_arn"), ev.get("product_code"),
            ev.get("offer_identifier"), ev.get("is_free_trial"),
            ev.get("timestamp"), json.dumps(ev.get("raw") or {}),
        )
        if inserted is None:
            log.info("duplicate delivery of %s — no state change", event_id)
            return True

        if channel == "eventbridge":
            label = ev.get("detail_type")
            if event_status(label) is None:
                log.warning("EventBridge %s: unhandled detail-type %r — stored, no state change",
                            event_id, label)
            matched, updated = await apply_license_event(
                conn, label, ev.get("acceptor_account_id"), ev.get("license_arn"),
                ev.get("product_code"), ev.get("timestamp"), ev.get("agreement_id"),
            )
        elif channel == "sns":
            label = ev.get("action")
            if label not in ACTION_STATUS:
                log.warning("SNS %s: unknown action %r — stored, no state change",
                            event_id, label)
            matched, updated = await apply_subscription_action(
                conn, label, ev.get("customer_identifier"),
                ev.get("product_code"), ev.get("timestamp"),
            )
        else:
            log.warning("%s: unrecognised envelope — stored for inspection", event_id)
            matched, updated = 0, 0

        if matched:
            await conn.execute(
                "UPDATE aws_marketplace_notifications "
                "SET matched = TRUE, matched_rows = $1 WHERE event_id = $2",
                matched, event_id,
            )
        if matched > 1:
            log.warning("%s (%s) matched %d agreements — applied to all",
                        event_id, label, matched)
        if not matched:
            log.warning("%s (%s) correlates to no subscriber — kept with matched=false",
                        event_id, label)
        elif not updated:
            log.info("%s (%s) is older than the recorded state — ignored", event_id, label)
        else:
            log.info("%s (%s) applied to %d subscriber row(s)", event_id, label, updated)
    return True


async def poll_once(sqs, conn):
    """One long-poll cycle. Returns the number of messages handled."""
    resp = await asyncio.to_thread(
        sqs.receive_message,
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=BATCH_SIZE,
        WaitTimeSeconds=WAIT_SECONDS,
        MessageAttributeNames=["All"],
    )
    messages = resp.get("Messages", [])
    for message in messages:
        if await handle_message(conn, message):
            await asyncio.to_thread(
                sqs.delete_message,
                QueueUrl=QUEUE_URL,
                ReceiptHandle=message["ReceiptHandle"],
            )
    return len(messages)


async def main():
    sqs = _sqs_client()
    conn = await asyncpg.connect(user=DB_USER, database=DB_NAME)
    log.info("polling %s", QUEUE_URL)
    try:
        while True:
            try:
                await poll_once(sqs, conn)
            except (ClientError, BotoCoreError) as e:
                log.error("SQS error: %s — backing off %ds", e, ERROR_BACKOFF)
                await asyncio.sleep(ERROR_BACKOFF)
            except asyncpg.PostgresError as e:
                log.error("database error: %s — backing off %ds", e, ERROR_BACKOFF)
                await asyncio.sleep(ERROR_BACKOFF)
    finally:
        await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("stopped")
