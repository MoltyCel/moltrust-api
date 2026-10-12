"""MolTrust Ads plans: Free and Pro, enforced at the /enforce boundary.

The enforcement kernel (app/enforcement/*) stays stateless and plan-blind. This
module sits in front of it for mandates with ``"profile": "ads"`` only:

- the caller must present an API key (a DID header proves nothing about who pays);
- the ad accounts a mandate covers are the union of the values of every
  ``enum`` constraint on field ``account_id`` across its grants;
- Free: at most FREE_AD_ACCOUNTS accounts and FREE_VERDICTS_PER_MONTH verdicts
  per API key per calendar month (UTC);
- Pro: an active ``ads_pro`` subscription bound to the key's payer_ref; the
  account allowance is the subscription quantity, no verdict cap beyond the
  endpoint's rate limit.

Over a limit the answer is HTTP 402 with a JSON body that names plan, limit and
upgrade path. The kernel is not called, so no PERMIT can come out over the cap.
Verdicts are counted per key fingerprint (never the key itself) in the
moltstack-owned table ``ads_verdict_meter``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Any, Optional

ADS_PROFILE = "ads"
ADS_PRO_TIER = "ads_pro"
FREE_AD_ACCOUNTS = 1
FREE_VERDICTS_PER_MONTH = 1000
PRO_MAX_AD_ACCOUNTS = 100
RATE_LIMIT = "60/minute"
UPGRADE_URL = "https://moltrust.ch/ads#pricing"
SIGNUP_HINT = ("Ads mandates need an API key: POST https://api.moltrust.ch/auth/signup "
               "with {\"email\": \"...\"} returns one (free plan). Send it as X-API-Key.")


async def ensure_ads_tables(conn) -> None:
    """Idempotent schema. Mirrors migrations/2026-10-12_ads_billing.sql."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads_verdict_meter (
            key_fp     TEXT        NOT NULL,
            month_key  TEXT        NOT NULL,
            verdicts   BIGINT      NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (key_fp, month_key)
        )
        """
    )


def key_fp(api_key: str) -> str:
    """Full SHA-256 of the key: identity for the meter, never the secret."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def month_key(now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m")


def is_ads_mandate(mandate: Any) -> bool:
    return isinstance(mandate, dict) and mandate.get("profile") == ADS_PROFILE


def mandate_account_ids(mandate: Any) -> set:
    """Union of the values of every enum constraint on account_id, across all grants.

    Malformed parts are skipped here; the kernel answers them with DENY."""
    out: set = set()
    grants = mandate.get("grants") if isinstance(mandate, dict) else None
    if not isinstance(grants, list):
        return out
    for g in grants:
        cons = g.get("constraints") if isinstance(g, dict) else None
        if not isinstance(cons, list):
            continue
        for c in cons:
            if (isinstance(c, dict) and c.get("type") == "enum"
                    and c.get("field") == "account_id" and isinstance(c.get("values"), list)):
                for v in c["values"]:
                    out.add(v if isinstance(v, (str, int, float, bool)) else repr(v))
    return out


async def pro_quantity(conn, payer_ref: Optional[str]) -> int:
    """Ad accounts paid for: summed quantity of the payer's active ads_pro subscriptions."""
    if not payer_ref:
        return 0
    q = await conn.fetchval(
        "SELECT COALESCE(SUM(COALESCE(quantity, 1)), 0) FROM billing_subscriptions "
        "WHERE payer_ref = $1 AND active = true AND tier = $2",
        payer_ref, ADS_PRO_TIER,
    )
    return int(q or 0)


async def plan_for_key(conn, api_key: str) -> dict:
    from app.accounts import payer_ref_for_key
    payer_ref = await payer_ref_for_key(conn, api_key)
    qty = await pro_quantity(conn, payer_ref)
    if qty > 0:
        return {"plan": "pro", "ad_accounts_allowed": qty, "verdicts_limit": None,
                "payer_ref": payer_ref}
    return {"plan": "free", "ad_accounts_allowed": FREE_AD_ACCOUNTS,
            "verdicts_limit": FREE_VERDICTS_PER_MONTH, "payer_ref": payer_ref}


async def verdicts_used(conn, api_key: str, mk: Optional[str] = None) -> int:
    v = await conn.fetchval(
        "SELECT verdicts FROM ads_verdict_meter WHERE key_fp = $1 AND month_key = $2",
        key_fp(api_key), mk or month_key(),
    )
    return int(v or 0)


def _limit_body(plan: dict, error: str, message: str, **extra) -> dict:
    body = {
        "error": error,
        "message": message,
        "plan": plan["plan"],
        "limit": {"ad_accounts": plan["ad_accounts_allowed"],
                  "verdicts_per_month": plan["verdicts_limit"]},
        "upgrade": UPGRADE_URL,
        "checkout": {"endpoint": "POST https://api.moltrust.ch/billing/checkout",
                     "body": {"tier": ADS_PRO_TIER, "quantity": "<number of ad accounts>"},
                     "auth": "X-API-Key"},
    }
    body.update(extra)
    return body


async def gate(conn, api_key: str, mandate: Any, now: Optional[_dt.datetime] = None):
    """Check the plan and count one verdict. Returns None to proceed, or
    (status, body) to answer instead of the kernel."""
    plan = await plan_for_key(conn, api_key)
    accounts = mandate_account_ids(mandate)
    if len(accounts) > plan["ad_accounts_allowed"]:
        return 402, _limit_body(
            plan, "ads_plan_ad_accounts_exceeded",
            f"The mandate covers {len(accounts)} ad accounts; the {plan['plan']} plan "
            f"allows {plan['ad_accounts_allowed']}. Upgrade or raise the quantity "
            f"(one Pro seat per ad account).",
            requested_ad_accounts=len(accounts))
    mk = month_key(now)
    fp = key_fp(api_key)
    if plan["verdicts_limit"] is None:
        await conn.execute(
            "INSERT INTO ads_verdict_meter (key_fp, month_key, verdicts) VALUES ($1, $2, 1) "
            "ON CONFLICT (key_fp, month_key) DO UPDATE SET "
            "verdicts = ads_verdict_meter.verdicts + 1, updated_at = now()",
            fp, mk)
        return None
    # Count and cap in one statement: no row back means the cap was already reached,
    # and nothing was counted for the refused call.
    row = await conn.fetchrow(
        "INSERT INTO ads_verdict_meter (key_fp, month_key, verdicts) VALUES ($1, $2, 1) "
        "ON CONFLICT (key_fp, month_key) DO UPDATE SET "
        "verdicts = ads_verdict_meter.verdicts + 1, updated_at = now() "
        "WHERE ads_verdict_meter.verdicts < $3 RETURNING verdicts",
        fp, mk, plan["verdicts_limit"])
    if row is None:
        return 402, _limit_body(
            plan, "ads_plan_verdicts_exhausted",
            f"The free plan includes {plan['verdicts_limit']} ads verdicts per calendar "
            f"month (UTC); {mk} is used up. Pro has no monthly verdict cap.",
            month=mk, verdicts_used=plan["verdicts_limit"])
    return None


async def plan_report(conn, api_key: str, now: Optional[_dt.datetime] = None) -> dict:
    """Body of GET /ads/plan."""
    plan = await plan_for_key(conn, api_key)
    mk = month_key(now)
    return {
        "plan": plan["plan"],
        "ad_accounts_allowed": plan["ad_accounts_allowed"],
        "verdicts_used": await verdicts_used(conn, api_key, mk),
        "verdicts_limit": plan["verdicts_limit"],
        "month": mk,
        "rate_limit": RATE_LIMIT,
        "upgrade": UPGRADE_URL if plan["plan"] == "free" else None,
        "manage": "POST https://api.moltrust.ch/billing/portal (X-API-Key)"
                  if plan["plan"] == "pro" else None,
    }
