"""AWS Marketplace SaaS fulfillment endpoint.

Runs on Hetzner (api.moltrust.ch) like everything else — no AWS infra. On a
buyer's first visit, AWS Marketplace POSTs `x-amzn-marketplace-token` here; we
resolve the buyer with marketplacemetering.resolve_customer using the SELLER
account (993604559884) credentials in AWS_MP_* — never the Hetzner server keys —
persist the subscriber, and return the AWS-required landing page.

resolve_customer notes: the token is valid 4h and the call is idempotent; the
response gives CustomerAWSAccountId + ProductCode. CustomerIdentifier is
deprecated and is deliberately not used.
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

_LANDING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Welcome to MolTrust</title>
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
  <h1>Welcome to MolTrust</h1>
  <p>Your AWS Marketplace subscription is registered. To finish setup, create your MolTrust account and connect your agents &mdash; billing runs through AWS.</p>
  <a class="btn" href="__SIGNUP__">Set up your MolTrust account &rarr;</a>
  <p class="sub">Support: <a href="mailto:__SUPPORT__">__SUPPORT__</a> &middot; MolTrust is the trust layer for the agent economy.</p>
</div></body></html>"""
_LANDING_HTML = _LANDING_HTML.replace("__SIGNUP__", SIGNUP_URL).replace("__SUPPORT__", SUPPORT_EMAIL)

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
    """Idempotent table creation (called at app startup, like ensure_billing_tables)."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aws_marketplace_subscribers (
            customer_aws_account_id TEXT NOT NULL,
            license_arn             TEXT,
            product_code            TEXT NOT NULL,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            moltrust_account_id     TEXT,
            PRIMARY KEY (customer_aws_account_id, product_code)
        )
        """
    )


def _marketplace_client():
    """boto3 client using the SELLER account keys (AWS_MP_*), never the default env keys."""
    ak = os.environ.get("AWS_MP_ACCESS_KEY_ID")
    sk = os.environ.get("AWS_MP_SECRET_ACCESS_KEY")
    if not ak or not sk:
        raise RuntimeError("AWS_MP credentials not configured")
    return boto3.client(
        "marketplacemetering",
        region_name=AWS_MP_REGION,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    )


def _resolve_customer(token: str) -> dict:
    """Blocking boto3 call; run in a threadpool from the async handler."""
    return _marketplace_client().resolve_customer(RegistrationToken=token)


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
    product_code = resp.get("ProductCode") or PRODUCT_CODE
    license_arn = resp.get("LicenseArn")  # not in a standard resolve_customer response -> nullable

    if account_id:
        import app.main as _m  # deferred: avoid an import cycle at module load
        pool = getattr(_m, "db_pool", None)
        if pool is not None:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO aws_marketplace_subscribers
                        (customer_aws_account_id, license_arn, product_code)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (customer_aws_account_id, product_code)
                    DO UPDATE SET license_arn = COALESCE(
                        EXCLUDED.license_arn, aws_marketplace_subscribers.license_arn)
                    """,
                    account_id, license_arn, product_code,
                )

    return HTMLResponse(_LANDING_HTML, status_code=200)
