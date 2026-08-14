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
    apply_subscription_action,
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


def parse_envelope(message):
    """Unwrap one SQS message into (sns_message_id, payload, sns_timestamp).

    Handles both the standard SNS envelope and raw message delivery, in case the
    subscription is ever switched to raw. Returns None if the body is not JSON.
    """
    try:
        body = json.loads(message.get("Body") or "")
    except (TypeError, ValueError):
        log.error("SQS message %s has a non-JSON body", message.get("MessageId"))
        return None

    if isinstance(body, dict) and body.get("Type") == "Notification" and "Message" in body:
        try:
            payload = json.loads(body["Message"])
        except (TypeError, ValueError):
            log.error("SNS envelope %s carries a non-JSON Message", body.get("MessageId"))
            return None
        return body.get("MessageId") or message.get("MessageId"), payload, _parse_timestamp(body.get("Timestamp"))

    # Raw delivery: the body is the payload itself, so fall back to the SQS id.
    if isinstance(body, dict):
        return message.get("MessageId"), body, None

    log.error("SQS message %s is JSON but not an object", message.get("MessageId"))
    return None


async def handle_message(conn, message):
    """Record and apply one message. Returns True if it may be deleted."""
    parsed = parse_envelope(message)
    if parsed is None:
        # Malformed and unfixable by retrying — leave it for the queue's redrive
        # policy rather than deleting evidence.
        return False
    sns_message_id, payload, sns_timestamp = parsed

    action = payload.get("action")
    customer_identifier = payload.get("customer-identifier")
    product_code = payload.get("product-code")
    offer_identifier = payload.get("offer-identifier")
    is_free_trial = _as_bool(payload.get("isFreeTrialTermPresent"))

    if action not in ACTION_STATUS:
        log.warning("unknown action %r in message %s — recorded, no state change",
                    action, sns_message_id)

    async with conn.transaction():
        inserted = await conn.fetchval(
            """
            INSERT INTO aws_marketplace_notifications
                (sns_message_id, action, customer_identifier, product_code,
                 offer_identifier, is_free_trial, sns_timestamp, raw)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            ON CONFLICT (sns_message_id) DO NOTHING
            RETURNING sns_message_id
            """,
            sns_message_id, action or "", customer_identifier, product_code,
            offer_identifier, is_free_trial, sns_timestamp, json.dumps(payload),
        )
        if inserted is None:
            log.info("duplicate delivery of %s — no state change", sns_message_id)
            return True

        matched, updated = await apply_subscription_action(
            conn, action, customer_identifier, product_code, sns_timestamp
        )
        if matched:
            await conn.execute(
                "UPDATE aws_marketplace_notifications "
                "SET matched = TRUE, matched_rows = $1 WHERE sns_message_id = $2",
                matched, sns_message_id,
            )
        if matched > 1:
            # Concurrent Agreements: the notification body has no agreement
            # discriminator, so every agreement of this customer moves together.
            log.warning(
                "message %s (%s) matched %d agreements for customer %s — "
                "applied to all; SNS carries no agreement id",
                sns_message_id, action, matched, customer_identifier,
            )
        if not matched:
            log.warning(
                "message %s (%s) has no subscriber for customer-identifier %r — "
                "kept with matched=false for replay",
                sns_message_id, action, customer_identifier,
            )
        elif not updated:
            log.info("message %s (%s) is older than the recorded state — ignored",
                     sns_message_id, action)
        else:
            log.info("message %s (%s) applied to %d subscriber row(s)",
                     sns_message_id, action, updated)
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
