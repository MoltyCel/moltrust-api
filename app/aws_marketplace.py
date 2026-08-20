"""AWS Marketplace SaaS fulfillment endpoint.

Runs on Hetzner (api.moltrust.ch) like everything else — no AWS infra. On a
buyer's first visit, AWS Marketplace POSTs `x-amzn-marketplace-token` here; we
resolve the buyer with meteringmarketplace.resolve_customer using the SELLER
account (993604559884) credentials in AWS_MP_* — never the Hetzner server keys —
persist the subscriber as `pending`, and return a holding page.

Access is granted only once the subscription notification queue reports
subscribe-success (agents/mp_subscription_consumer.py). AWS is explicit that a
buyer reaching this endpoint is not yet a paying customer: subscribe-fail can
still follow, so resources must not be provisioned on the strength of
ResolveCustomer alone.

resolve_customer notes: the token is valid 4h and the call is idempotent; the
response gives CustomerAWSAccountId, ProductCode and LicenseArn.
CustomerIdentifier is not populated for new SaaS integrations — but it is the
ONLY identifier the SNS notification body carries, so it is persisted whenever
AWS does return it. Without it a notification cannot be correlated to a
subscriber and lands in aws_marketplace_notifications with matched=false.
"""
import os
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse
from fastapi.concurrency import run_in_threadpool

log = logging.getLogger("aws_marketplace")
router = APIRouter(tags=["AWS Marketplace"])

AWS_MP_REGION = "us-east-1"
PRODUCT_CODE = "74az0btybm649octamy0sktos"
SIGNUP_URL = "https://moltrust.ch/developers.html"
SUPPORT_EMAIL = "support@moltrust.ch"

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_CANCELLING = "cancelling"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"

# Subscription notification actions -> subscriber state.
#
# Field names and action values below are taken from the AWS documentation
# ("Amazon SNS notifications for SaaS products"), NOT verified against a live
# message: the queue was empty at build time. Treat as documented-but-unproven
# until the first real notification lands.
#
# unsubscribe-pending opens a ~1h window in which AWS still accepts final
# metering records. MolTrust meters externally (Stripe), so this state is
# recorded and logged but triggers no BatchMeterUsage call.
# EventBridge detail-type -> subscriber state. AWS suffixes every type with
# " - Manufacturer" or " - Proposer" depending on the seller role, so the
# lookup strips the suffix rather than enumerating both.
#
# saas-product-customer-setup step 9 is explicit about which one activates:
#   "Do not activate a product subscription unless you receive a
#    License Updated event."
# Purchase Agreement Created therefore confirms the record but does NOT grant
# entitlement, and the signup itself is not gated on any of these at all.
EVENT_STATUS = {
    "License Updated": STATUS_ACTIVE,
    "License Deprovisioned": STATUS_CANCELLED,
    "Purchase Agreement Ended": STATUS_CANCELLED,
    "Purchase Agreement Created": STATUS_PENDING,
}


def event_status(detail_type):
    """Map an EventBridge detail-type to a state, ignoring the role suffix."""
    if not detail_type:
        return None
    base = detail_type.split(" - ")[0].strip()
    return EVENT_STATUS.get(base)


ACTION_STATUS = {
    "subscribe-success": STATUS_ACTIVE,
    "subscribe-fail": STATUS_FAILED,
    "unsubscribe-pending": STATUS_CANCELLING,
    "unsubscribe-success": STATUS_CANCELLED,
}

_PAGE_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:#0F172A;color:#F8FAFC;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{max-width:560px;padding:2.5rem 2rem;text-align:center}
  h1{margin:0 0 .6rem;font-size:1.7rem}
  p{color:#CBD5E1;line-height:1.65}
  a{color:#E85D26}
  a.btn{display:inline-block;margin-top:1.6rem;background:#E85D26;color:#fff;text-decoration:none;padding:.75rem 1.7rem;border-radius:8px;font-weight:600}
  .sub{font-size:.85rem;color:#94A3B8;margin-top:1.9rem}
</style></head>
<body><div class="card">
__BODY__
  <p class="sub">Support: <a href="mailto:__SUPPORT__">__SUPPORT__</a> &middot; MolTrust is the trust layer for the agent economy.</p>
</div></body></html>"""

_LANDING_BODY = """  <h1>Welcome to MolTrust</h1>
  <p>Your AWS Marketplace subscription is confirmed. To finish setup, create your MolTrust account and connect your agents &mdash; billing runs through AWS.</p>
  <a class="btn" href="__SIGNUP__">Set up your MolTrust account &rarr;</a>"""

# Only reachable when ResolveCustomer returns no account id — the buyer is not
# held here in the normal flow. There is deliberately no link back to this URL:
# the route is POST-only, and an <a href=""> issued a GET, which returned 405
# "Method Not Allowed" to buyers who clicked it during the 2026-08-19 review.
_PENDING_BODY = """  <h1>We could not finish setting up your subscription</h1>
  <p>Your AWS Marketplace purchase went through, but we could not read the
  registration details. Return to AWS Marketplace and choose &ldquo;Set up your
  account&rdquo; again, or get in touch and we will finish it for you.</p>"""

_CANCELLED_BODY = """  <h1>This subscription has ended</h1>
  <p>Your AWS Marketplace subscription for MolTrust is no longer active. If you
  believe this is wrong, or you would like to resubscribe, get in touch.</p>"""

_LANDING_HTML = _PAGE_SHELL.replace("__TITLE__", "Welcome to MolTrust").replace(
    "__BODY__", _LANDING_BODY
).replace("__SUPPORT__", SUPPORT_EMAIL)  # __SIGNUP__ rendered per-request (carries aws_ref)

_PENDING_HTML = _PAGE_SHELL.replace("__TITLE__", "MolTrust — confirming your subscription").replace(
    "__BODY__", _PENDING_BODY
).replace("__SUPPORT__", SUPPORT_EMAIL)

_CANCELLED_HTML = _PAGE_SHELL.replace("__TITLE__", "MolTrust").replace(
    "__BODY__", _CANCELLED_BODY
).replace("__SUPPORT__", SUPPORT_EMAIL)

_TOKEN_ERROR_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>MolTrust</title></head>"
    "<body style=\"font-family:system-ui,sans-serif;max-width:560px;margin:4rem auto;padding:0 1rem;color:#0F172A\">"
    "<h1>We couldn't verify your subscription token</h1>"
    "<p>AWS Marketplace tokens are valid for 4 hours. Please return to AWS Marketplace "
    "and click &ldquo;Set up your account&rdquo; again. If the problem persists, contact "
    + SUPPORT_EMAIL + ".</p></body></html>"
)


async def ensure_aws_marketplace_tables(conn):
    """Idempotent schema creation (called at app startup, like ensure_billing_tables).

    Kept in sync with app/migrations/014_aws_subscription_state.sql — fresh
    databases get the current shape here, existing ones are migrated by the
    ALTERs below.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aws_marketplace_subscribers (
            customer_aws_account_id TEXT NOT NULL,
            license_arn             TEXT,
            product_code            TEXT NOT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            moltrust_account_id     TEXT
        )
        """
    )
    # D (2026-07-17): persist BOTH ResolveCustomer values — additive column for
    # CustomerIdentifier alongside the existing customer_aws_account_id.
    await conn.execute(
        "ALTER TABLE aws_marketplace_subscribers ADD COLUMN IF NOT EXISTS customer_identifier TEXT"
    )
    await conn.execute(
        "ALTER TABLE aws_marketplace_subscribers "
        "ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'pending'"
    )
    await conn.execute(
        "ALTER TABLE aws_marketplace_subscribers "
        "ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ"
    )
    await conn.execute(
        "ALTER TABLE aws_marketplace_subscribers "
        "ADD COLUMN IF NOT EXISTS id BIGINT GENERATED BY DEFAULT AS IDENTITY"
    )
    # Concurrent Agreements: several agreements may share (account, product),
    # so the old composite primary key has to go.
    await conn.execute(
        "ALTER TABLE aws_marketplace_subscribers "
        "DROP CONSTRAINT IF EXISTS aws_marketplace_subscribers_pkey"
    )
    await conn.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint
                           WHERE conname = 'aws_marketplace_subscribers_pkey') THEN
                ALTER TABLE aws_marketplace_subscribers
                    ADD CONSTRAINT aws_marketplace_subscribers_pkey PRIMARY KEY (id);
            END IF;
        END $$
        """
    )
    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS aws_mp_subscribers_agreement_uq
            ON aws_marketplace_subscribers
               (customer_aws_account_id, product_code, COALESCE(license_arn, ''))
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS aws_mp_subscribers_cust_ident_idx
            ON aws_marketplace_subscribers (customer_identifier, product_code)
            WHERE customer_identifier IS NOT NULL
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aws_marketplace_notifications (
            event_id      TEXT PRIMARY KEY,
            action              TEXT NOT NULL,
            customer_identifier TEXT,
            product_code        TEXT,
            offer_identifier    TEXT,
            is_free_trial       BOOLEAN,
            sns_timestamp       TIMESTAMPTZ,
            received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            matched             BOOLEAN NOT NULL DEFAULT FALSE,
            matched_rows        INTEGER NOT NULL DEFAULT 0,
            raw                 JSONB NOT NULL
        )
        """
    )
    for _col in (
        "channel TEXT NOT NULL DEFAULT 'sns'",
        "detail_type TEXT",
        "event_source TEXT",
        "agreement_id TEXT",
        "license_arn TEXT",
        "acceptor_account_id TEXT",
    ):
        await conn.execute(
            "ALTER TABLE aws_marketplace_notifications ADD COLUMN IF NOT EXISTS " + _col
        )
    await conn.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'aws_marketplace_notifications'
                         AND column_name = 'event_id') THEN
                ALTER TABLE aws_marketplace_notifications
                    RENAME COLUMN event_id TO event_id;
            END IF;
        END $$
        """
    )
    await conn.execute(
        "ALTER TABLE aws_marketplace_notifications ALTER COLUMN action DROP NOT NULL"
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS aws_mp_notifications_acceptor_idx
            ON aws_marketplace_notifications (acceptor_account_id, product_code)
            WHERE NOT matched
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS aws_mp_notifications_unmatched_idx
            ON aws_marketplace_notifications (customer_identifier, product_code)
            WHERE NOT matched
        """
    )


def _marketplace_client():
    """boto3 client using the SELLER account keys (AWS_MP_*), never the default env keys."""
    ak = os.environ.get("AWS_MP_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_MP_SECRET_ACCESS_KEY")
    if not ak or not sk:
        raise RuntimeError("AWS_MP credentials not configured")
    return boto3.client(
        "meteringmarketplace",
        region_name=AWS_MP_REGION,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    )


def _resolve_customer(token: str) -> dict:
    """Blocking boto3 call; run in a threadpool from the async handler."""
    return _marketplace_client().resolve_customer(RegistrationToken=token)


async def apply_subscription_action(conn, action, customer_identifier, product_code, sns_timestamp):
    """Apply one notification to the subscriber rows it identifies.

    Returns (matched_rows, updated_rows). matched_rows counts subscribers the
    notification correlates to regardless of ordering; updated_rows counts those
    actually moved, which is 0 for an out-of-order (stale) delivery. The two are
    reported separately so a stale event is not mistaken for an uncorrelated one.

    SQS does not preserve order, so a transition is applied only when its SNS
    timestamp is newer than the state it would overwrite. Without that guard a
    late subscribe-success would silently revive a cancelled subscription.
    """
    status = ACTION_STATUS.get(action)
    if status is None or not customer_identifier:
        return 0, 0

    matched = await conn.fetchval(
        """
        SELECT count(*) FROM aws_marketplace_subscribers
        WHERE customer_identifier = $1 AND product_code = $2
        """,
        customer_identifier, product_code,
    )
    if not matched:
        return 0, 0

    updated = await conn.fetch(
        """
        UPDATE aws_marketplace_subscribers
           SET subscription_status = $1,
               status_updated_at   = $2
         WHERE customer_identifier = $3
           AND product_code = $4
           AND (status_updated_at IS NULL OR status_updated_at < $2)
        RETURNING id
        """,
        status, sns_timestamp, customer_identifier, product_code,
    )
    return matched, len(updated)


async def apply_license_event(conn, detail_type, acceptor_account_id,
                             license_arn, product_code, event_time):
    """Apply one EventBridge event to the subscriber rows it identifies.

    Correlates on acceptor.accountId, which every agreement and license event
    carries, rather than on license.arn, which only the license events do — a
    Purchase Agreement Created has no ARN at all. When an ARN is present it
    narrows the match to that one agreement; otherwise every agreement of that
    account for this product moves together.

    The ARN is compared on its trailing identifier, not as a whole string:
    ResolveCustomer returns
      arn:aws:license-manager::294406891311:license:l-51b9c...
    while the documented event example shows
      aws:license-manager:us-east-1:123456789012:l-e52ca...
    — different prefix, different region position, different separator.

    Returns (matched_rows, updated_rows), reported separately so a stale event
    is not mistaken for an uncorrelated one.
    """
    status = event_status(detail_type)
    if status is None or not acceptor_account_id:
        return 0, 0

    tail = license_arn.rsplit(":", 1)[-1] if license_arn else None

    # Both statements spell the match condition out instead of sharing a
    # concatenated fragment: a literal query reads at the call site, and it
    # keeps bandit's B608 off a construction that only looked like
    # interpolation. Cheaper than another # nosec to justify.
    matched = await conn.fetchval(
        """
        SELECT count(*) FROM aws_marketplace_subscribers
         WHERE customer_aws_account_id = $1
           AND product_code = $2
           AND ($3::text IS NULL OR license_arn LIKE '%' || $3)
        """,
        acceptor_account_id, product_code, tail,
    )
    if not matched:
        return 0, 0

    updated = await conn.fetch(
        """
        UPDATE aws_marketplace_subscribers
           SET subscription_status = $4,
               status_updated_at   = $5
         WHERE customer_aws_account_id = $1
           AND product_code = $2
           AND ($3::text IS NULL OR license_arn LIKE '%' || $3)
           AND (status_updated_at IS NULL OR status_updated_at < $5)
        RETURNING id
        """,
        acceptor_account_id, product_code, tail, status, event_time,
    )
    return matched, len(updated)


async def _replay_unmatched(conn, customer_identifier, product_code):
    """Apply notifications that arrived before this subscriber existed.

    The consumer keeps uncorrelated events with matched=false rather than
    dropping them; once ResolveCustomer creates the row they are replayed here
    in timestamp order.
    """
    pending = await conn.fetch(
        """
        SELECT event_id, action, sns_timestamp
          FROM aws_marketplace_notifications
         WHERE NOT matched
           AND customer_identifier = $1
           AND product_code = $2
         ORDER BY sns_timestamp NULLS FIRST
        """,
        customer_identifier, product_code,
    )
    for ev in pending:
        matched, _ = await apply_subscription_action(
            conn, ev["action"], customer_identifier, product_code, ev["sns_timestamp"]
        )
        if matched:
            await conn.execute(
                "UPDATE aws_marketplace_notifications "
                "SET matched = TRUE, matched_rows = $1 WHERE event_id = $2",
                matched, ev["event_id"],
            )
            log.info("replayed notification %s (%s) for %s",
                     ev["event_id"], ev["action"], customer_identifier)


async def _persist_subscriber(conn, account_id, customer_identifier, license_arn, product_code):
    """Upsert the agreement row and return its current subscription status."""
    # The 2026-08-19 purchase produced two rows for one agreement: the first
    # ResolveCustomer response carried no LicenseArn, the second did, and the
    # unique index treats NULL and an ARN as different agreements. Fill the
    # ARN-less row first; only a genuinely new agreement reaches the insert.
    if license_arn:
        await conn.execute(
            """
            UPDATE aws_marketplace_subscribers
               SET license_arn = $3
             WHERE customer_aws_account_id = $1 AND product_code = $2
               AND license_arn IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM aws_marketplace_subscribers x
                    WHERE x.customer_aws_account_id = $1 AND x.product_code = $2
                      AND x.license_arn = $3)
            """,
            account_id, product_code, license_arn,
        )
    row = await conn.fetchrow(
        """
        INSERT INTO aws_marketplace_subscribers
            (customer_aws_account_id, customer_identifier, license_arn, product_code)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (customer_aws_account_id, product_code, COALESCE(license_arn, ''))
        DO UPDATE SET
            customer_identifier = COALESCE(
                EXCLUDED.customer_identifier, aws_marketplace_subscribers.customer_identifier)
        RETURNING subscription_status
        """,
        account_id, customer_identifier, license_arn, product_code,
    )
    # A repeat visit must not knock an already-confirmed subscriber back to
    # pending, so subscription_status is deliberately left untouched above.
    if customer_identifier:
        await _replay_unmatched(conn, customer_identifier, product_code)
        row = await conn.fetchrow(
            """
            SELECT subscription_status FROM aws_marketplace_subscribers
             WHERE customer_aws_account_id = $1 AND product_code = $2
               AND COALESCE(license_arn, '') = COALESCE($3::text, '')
            """,
            account_id, product_code, license_arn,
        )
    return row["subscription_status"] if row else STATUS_PENDING


@router.post("/aws/fulfillment", response_class=HTMLResponse)
async def aws_fulfillment(
    x_amzn_marketplace_token: str = Form(..., alias="x-amzn-marketplace-token"),
):
    try:
        resp = await run_in_threadpool(_resolve_customer, x_amzn_marketplace_token)
    except RuntimeError:
        log.error("AWS_MP credentials not configured — cannot fulfill marketplace token")
        return HTMLResponse(
            "<h1>MolTrust marketplace integration is not configured yet.</h1>"
            "<p>Please contact " + SUPPORT_EMAIL + ".</p>",
            status_code=503,
        )
    except (ClientError, BotoCoreError) as e:
        log.warning("resolve_customer failed: %s", e)
        return HTMLResponse(_TOKEN_ERROR_HTML, status_code=400)

    account_id = resp.get("CustomerAWSAccountId")
    # Persisted because the SNS notification body carries no AWS account id —
    # customer_identifier is the only join key back to this record. AWS leaves
    # it empty on new integrations; the consumer then reports matched=false.
    customer_identifier = resp.get("CustomerIdentifier")
    product_code = resp.get("ProductCode") or PRODUCT_CODE
    # Per-agreement identifier, and required by BatchMeterUsage should metering
    # ever move to AWS. Stored for that and for Concurrent Agreements.
    license_arn = resp.get("LicenseArn")

    status = STATUS_PENDING
    if account_id:
        import app.main as _m  # deferred: avoid an import cycle at module load
        pool = getattr(_m, "db_pool", None)
        if pool is not None:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    status = await _persist_subscriber(
                        conn, account_id, customer_identifier, license_arn, product_code
                    )
        if not customer_identifier:
            log.warning(
                "ResolveCustomer returned no CustomerIdentifier for account %s — "
                "subscription notifications cannot be correlated to this record",
                account_id,
            )
    else:
        log.error("ResolveCustomer returned no CustomerAWSAccountId — nothing persisted")

    if status in (STATUS_CANCELLED, STATUS_FAILED):
        return HTMLResponse(_CANCELLED_HTML, status_code=200)
    if not account_id:
        return HTMLResponse(_PENDING_HTML, status_code=200)

    # Signup is NOT gated on a notification. AWS requires the buyer to be able
    # to create an account and reach the product straight after subscribing,
    # and the onboarding guide puts account creation (step 7) before the
    # License Updated gate (step 9). Holding the CTA behind the notification
    # conflated the two and produced the "Confirming your subscription" dead
    # end the 2026-08-19 review flagged. subscription_status stays as
    # bookkeeping — set by apply_license_event, read for revocation — not as a
    # lock on the signup.
    #
    # Attribution: carry the resolved AWS account id into the signup CTA so the
    # eventual account can stamp accounts.aws_customer_identifier. Billing
    # still runs via Stripe.
    sep = "&" if "?" in SIGNUP_URL else "?"
    signup_url = f"{SIGNUP_URL}{sep}aws_ref={account_id}" if account_id else SIGNUP_URL
    return HTMLResponse(_LANDING_HTML.replace("__SIGNUP__", signup_url), status_code=200)
