"""
MolTrust Billing Integration — Stripe
billing.py — FastAPI Router

Produkte (Early Access; USD default + EUR, Numeral-Paritaet):
  - MolTrust Professional  $99 / EUR 99/Monat   -> 10'000 Credits/Monat
  - MolTrust Scale         $299 / EUR 299/Monat -> 30'000 Credits/Monat

Env vars required (aus ~/.moltrust_secrets):
  STRIPE_SECRET_KEY      sk_test_... (→ sk_live_... im Live-Betrieb)
  STRIPE_WEBHOOK_SECRET  whsec_...   (nach Webhook-Registrierung)
"""

import os
import secrets
import logging
import re
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from app.credits import grant_credits, ensure_balance_row

REF_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# ── Stripe init ─────────────────────────────────────────────────────────────
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

logger = logging.getLogger("billing")
router = APIRouter(prefix="/billing", tags=["billing"])
admin_router = APIRouter(prefix="/admin/billing", tags=["billing-admin"])

# -- Stripe error mapping ---------------------------------------------------
# Stripe raises one exception family for two very different things: input the
# caller can fix (unknown customer, price/currency mismatch) and faults on our
# side (bad API key, provider down). Unmapped, both reach the global handler in
# main.py as an opaque 500 - which is how every EUR checkout and every scanner
# hit on /billing/subscription/<junk> ended up logged as a server error.
StripeError = stripe.StripeError


async def stripe_error_handler(request, exc):
    """Map a Stripe exception onto the status code it actually deserves."""
    if isinstance(exc, stripe.InvalidRequestError):
        # resource_missing = the caller named something that does not exist.
        status = 404 if getattr(exc, "code", None) == "resource_missing" else 400
        detail = str(getattr(exc, "user_message", None) or exc)
    elif isinstance(exc, stripe.CardError):
        status, detail = 402, str(getattr(exc, "user_message", None) or exc)
    elif isinstance(exc, stripe.RateLimitError):
        status, detail = 429, "Payment provider rate limit - retry shortly."
    else:
        # AuthenticationError / APIConnectionError / APIError: ours, not theirs.
        # Keep the detail in the log, not in the response.
        status, detail = 502, "Payment provider unavailable"
    logger.error(
        "Stripe %s on %s %s: %s",
        type(exc).__name__, request.method, request.url.path, exc,
    )
    return JSONResponse(status_code=status, content={"error": detail})


# ── Tier definitions ─────────────────────────────────────────────────────────
SLOT_LOOKUP_KEY = "mt_v2_slot_monthly"  # load-bearing binding for the $9 add-on slot; price resolved live by lookup_key, not a hardcoded price_id

TIERS = {
    "base": {
        "name": "MolTrust Base",
        "price": 19,
        "included_slots": 2,
        "addon_slot_price": 9,
        "retention_months": 12,
        "lookup_key": "mt_v2_base_monthly",
        "annual_lookup_key": "mt_v2_base_annual",
        "sla": "99.5%",
    },
    "scale": {
        "name": "MolTrust Scale",
        "price": 299,
        "included_slots": 75,
        "overage_per_slot": 3.5,
        "retention_months": 12,
        "lookup_key": "mt_v2_scale_monthly",
        "annual_lookup_key": "mt_v2_scale_annual",
        "sla": "99.9%",
    },
    "slot": {
        "name": "MolTrust Additional Agent Slot",
        "price": 9,
        "included_slots": 1,
        "retention_months": 12,
        "lookup_key": SLOT_LOOKUP_KEY,
        "sla": "99.5%",
    },
}
# ── DB table setup ───────────────────────────────────────────────────────────
async def ensure_billing_tables(conn):
    """Create billing tables if they don't exist."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS billing_subscriptions (
            stripe_subscription_id TEXT PRIMARY KEY,
            stripe_customer_id     TEXT NOT NULL,
            tier                   TEXT NOT NULL,
            agent_did              TEXT,
            active                 BOOLEAN NOT NULL DEFAULT true,
            current_period_end     TIMESTAMPTZ,
            cancel_at_period_end   BOOLEAN NOT NULL DEFAULT false,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        ALTER TABLE billing_subscriptions
        ADD COLUMN IF NOT EXISTS referral_source TEXT
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_billing_sub_referral
        ON billing_subscriptions(referral_source)
        WHERE referral_source IS NOT NULL
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS billing_payments (
            stripe_invoice_id      TEXT PRIMARY KEY,
            stripe_customer_id     TEXT NOT NULL,
            amount_chf             NUMERIC(10,2) NOT NULL DEFAULT 0,
            success                BOOLEAN NOT NULL,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_billing_sub_customer
        ON billing_subscriptions(stripe_customer_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_billing_pay_customer
        ON billing_payments(stripe_customer_id)
    """)
# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CheckoutRequest(BaseModel):
    tier: str
    currency: str = "usd"
    email: Optional[EmailStr] = None
    agent_did: Optional[str] = None
    payer_ref: Optional[str] = None
    ref: Optional[str] = Field(
        default=None,
        description="Optional referral source tag (e.g. 'dsncon'). Stored on the subscription for attribution.",
    )
    success_url: str = "https://moltrust.ch/billing/success"
    cancel_url: str = "https://moltrust.ch/billing/cancel"
class PortalRequest(BaseModel):
    customer_id: str
    return_url: str = "https://moltrust.ch/dashboard"
# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/plans")
async def list_plans():
    """Public: return all available plans."""
    return {"plans": TIERS, "currencies": ["usd", "eur"]}
@router.post("/checkout")
async def create_checkout(req: CheckoutRequest):
    """
    Create a Stripe Checkout Session.
    Returns {checkout_url} — redirect customer there.
    """
    if req.tier not in TIERS:
        raise HTTPException(400, f"Unknown tier: {req.tier}. Use: {list(TIERS)}")

    currency = (req.currency or "").lower()
    if currency not in {"usd", "eur"}:
        raise HTTPException(400, f"Unsupported currency: {req.currency}. Use one of: usd, eur.")

    tier_info = TIERS[req.tier]

    # Match the price for the requested surface and FORCE that currency on the
    # Checkout Session: /pricing (usd) presents USD $99 to everyone (the buyer's bank
    # does the FX, ~€86 on the statement); /compliance (eur) presents EUR €99 flat
    # (intentional EU-compliance pricing). We deliberately do NOT auto-present the
    # buyer's local currency: the USD prices still carry flat currency_options
    # (EUR/CHF/GBP 9900) that cannot be removed via the API and whose flat amounts
    # (€99 > $99 after FX) would be wrong for a USD-based plan. Forcing the session
    # currency renders those currency_options inert. (Adaptive Pricing is unavailable:
    # it needs the price currency to be a settlement currency, but this account
    # settles CHF only — see docs/billing-config.md.)
    # v2 catalogue is USD-only; select the exact price by lookup_key
    # (products carry monthly/annual/overage prices, so product-name is ambiguous).
    lk = tier_info.get("lookup_key")
    pl = stripe.Price.list(lookup_keys=[lk], active=True, limit=1)
    price_id = pl.data[0].id if pl.data else None
    if not price_id:
        raise HTTPException(
            500,
            f"No active Stripe price for tier '{req.tier}' (lookup_key={lk})."
        )

    # Normalize referral tag: lowercase, restricted charset, truncate
    ref = (req.ref or "").strip().lower()
    if ref and not REF_RE.match(ref):
        raise HTTPException(400, "Invalid ref: use 1–64 chars of [a-z0-9_-]")

    # Customer: lookup by email or let Stripe collect it
    customer_kwargs = {}
    if req.email:
        customers = stripe.Customer.list(email=req.email, limit=1)
        if customers.data:
            customer_kwargs["customer"] = customers.data[0].id
            # Stamp referral on existing Customer only if not already set (first-touch)
            if ref:
                existing = customers.data[0]
                if not (existing.metadata or {}).get("referral_source"):
                    stripe.Customer.modify(
                        existing.id,
                        metadata={**(existing.metadata or {}), "referral_source": ref},
                    )
        else:
            cust_metadata = {"agent_did": req.agent_did or "", "payer_ref": req.payer_ref or ""}
            if ref:
                cust_metadata["referral_source"] = ref
            cust = stripe.Customer.create(
                email=req.email,
                metadata=cust_metadata,
            )
            customer_kwargs["customer"] = cust.id

    session_metadata = {
        "tier": req.tier,
        "agent_did": req.agent_did or "",
        "payer_ref": req.payer_ref or "",
    }
    sub_metadata = {
        "tier": req.tier,
        "agent_did": req.agent_did or "",
        "payer_ref": req.payer_ref or "",
    }
    if ref:
        session_metadata["referral_source"] = ref
        sub_metadata["referral_source"] = ref

    session = stripe.checkout.Session.create(
        **customer_kwargs,
        mode="subscription",
        currency=currency,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=req.success_url + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=req.cancel_url,
        metadata=session_metadata,
        subscription_data={"metadata": sub_metadata},
    )

    logger.info(
        "Checkout created: %s tier=%s email=%s ref=%s",
        session.id, req.tier, req.email, ref or "-",
    )
    return {"checkout_url": session.url, "session_id": session.id}
@router.post("/portal")
async def customer_portal(req: PortalRequest):
    """Create a Stripe Customer Portal session for self-service management."""
    portal = stripe.billing_portal.Session.create(
        customer=req.customer_id,
        return_url=req.return_url,
    )
    return {"portal_url": portal.url}
@router.get("/subscription/{customer_id}")
async def get_subscription(customer_id: str):
    """Get current subscription status for a customer."""
    subs = stripe.Subscription.list(customer=customer_id, status="active", limit=1)
    if not subs.data:
        return {"active": False, "tier": None}

    sub = subs.data[0]
    tier = sub.metadata.get("tier", "unknown")
    return {
        "active": True,
        "tier": tier,
        "tier_info": TIERS.get(tier),
        "current_period_end": datetime.fromtimestamp(
            sub.current_period_end, tz=timezone.utc
        ).isoformat(),
        "cancel_at_period_end": sub.cancel_at_period_end,
        "stripe_subscription_id": sub.id,
        "stripe_customer_id": customer_id,
    }
# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK  —  https://api.moltrust.ch/billing/webhook
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Stripe sends signed events here.
    Verifies signature → updates billing tables via asyncpg pool.
    """
    from app.main import db_pool

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, WEBHOOK_SECRET
        )
    except stripe.SignatureVerificationError:
        logger.warning("Webhook signature verification failed")
        raise HTTPException(400, "Invalid signature")
    except Exception as e:
        logger.error("Webhook error: %s", e)
        raise HTTPException(400, str(e))

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info("Stripe webhook: %s id=%s", event_type, event["id"])

    async with db_pool.acquire() as conn:
        if event_type in (
            "customer.subscription.created",
            "customer.subscription.updated",
        ):
            await _upsert_subscription(conn, data, active=True)

        elif event_type == "customer.subscription.deleted":
            await _upsert_subscription(conn, data, active=False)

        elif event_type == "invoice.payment_succeeded":
            logger.info(
                "Payment OK: customer=%s amount=%.2f %s",
                data.get("customer"),
                data.get("amount_paid", 0) / 100,
                (data.get("currency") or "").upper(),
            )
            inserted = await _log_payment(conn, data, success=True)
            # Idempotent: grant only on first insert (no double-grant on Stripe
            # retries); covers first signup AND renewal. No grant in
            # customer.subscription.created (would double).
            if inserted and data.get("subscription"):
                await _grant_monthly_credits(conn, data["subscription"], data["id"])

        elif event_type == "invoice.payment_failed":
            logger.warning("Payment FAILED: customer=%s", data.get("customer"))
            await _log_payment(conn, data, success=False)

    return JSONResponse({"received": True})
# ═══════════════════════════════════════════════════════════════════════════════
# DB HELPERS (asyncpg)
# ═══════════════════════════════════════════════════════════════════════════════

async def _upsert_subscription(conn, sub: dict, active: bool):
    """Insert or update billing_subscriptions table."""
    meta = sub.get("metadata") or {}
    tier = meta.get("tier", "unknown")
    agent_did = meta.get("agent_did") or None
    payer_ref = meta.get("payer_ref") or None
    referral_source = meta.get("referral_source") or None

    # Fallback: if subscription metadata is missing referral_source, look it up on the Customer
    if not referral_source and sub.get("customer"):
        try:
            customer = stripe.Customer.retrieve(sub["customer"])
            referral_source = (customer.metadata or {}).get("referral_source") or None
        except Exception as e:
            logger.warning("Could not fetch customer %s for referral lookup: %s", sub.get("customer"), e)

    await conn.execute("""
        INSERT INTO billing_subscriptions
            (stripe_subscription_id, stripe_customer_id, tier, agent_did,
             payer_ref, active, current_period_end, cancel_at_period_end,
             referral_source, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, to_timestamp($7), $8, $9, NOW())
        ON CONFLICT (stripe_subscription_id) DO UPDATE SET
            tier                 = EXCLUDED.tier,
            payer_ref            = COALESCE(billing_subscriptions.payer_ref, EXCLUDED.payer_ref),
            active               = EXCLUDED.active,
            current_period_end   = EXCLUDED.current_period_end,
            cancel_at_period_end = EXCLUDED.cancel_at_period_end,
            referral_source      = COALESCE(billing_subscriptions.referral_source, EXCLUDED.referral_source),
            updated_at           = NOW()
    """,
        sub["id"],
        sub["customer"],
        tier,
        agent_did,
        payer_ref,
        active,
        sub.get("current_period_end"),
        sub.get("cancel_at_period_end", False),
        referral_source,
    )
    # Bind the paying Stripe customer to the account (accounts.stripe_customer_id).
    if payer_ref and sub.get("customer"):
        from app import accounts as _accounts
        await _accounts.bind_stripe_customer(conn, payer_ref, sub["customer"])
    logger.info(
        "Subscription upserted: %s tier=%s active=%s ref=%s",
        sub["id"], tier, active, referral_source or "-",
    )
async def _log_payment(conn, invoice: dict, success: bool) -> bool:
    """Log payment event. Returns True iff a NEW row was inserted (False on
    conflict / Stripe retry) — used for idempotent credit grants."""
    row = await conn.fetchrow("""
        INSERT INTO billing_payments
            (stripe_invoice_id, stripe_customer_id, amount_chf, success, created_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (stripe_invoice_id) DO NOTHING
        RETURNING stripe_invoice_id
    """,
        invoice["id"],
        invoice["customer"],
        invoice.get("amount_paid", 0) / 100,
        success,
    )
    return row is not None


async def _grant_monthly_credits(conn, subscription_id: str, invoice_id: str):
    """Grant the tier's monthly credits — covers first signup AND each renewal.
    Best-effort: logs and returns on any missing data, never crashes the webhook."""
    try:
        sub = stripe.Subscription.retrieve(subscription_id)
    except Exception as e:
        logger.warning("grant_monthly_credits: retrieve %s failed: %s", subscription_id, e)
        return
    meta = sub.metadata or {}
    tier = meta.get("tier")
    did = meta.get("agent_did") or None
    credits = TIERS.get(tier, {}).get("monthly_credits")
    if not did or not credits:
        logger.warning("grant_monthly_credits: skip sub=%s tier=%s did=%s credits=%s",
                       subscription_id, tier, did, credits)
        return
    await ensure_balance_row(conn, did)
    await grant_credits(conn, did, credits, reference=invoice_id,
                        description=f"Subscription {tier} monthly credits")
    logger.info("Granted %s credits to %s (tier=%s invoice=%s)", credits, did, tier, invoice_id)


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN  —  referral attribution (no commission calculation, just tracking)
# ═══════════════════════════════════════════════════════════════════════════════

@admin_router.get("/referrals")
async def list_referrals(request: Request):
    """
    Return subscription counts and MRR (CHF) grouped by referral_source.
    Requires x-admin-key header matching ADMIN_KEY env var.
    """
    admin_key = request.headers.get("x-admin-key", "")
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or not admin_key or not secrets.compare_digest(admin_key, expected):
        raise HTTPException(401, "Admin key required")

    from app.main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    # Build tier→price lookup as VALUES rows so MRR is summed in SQL
    tier_values = ",".join(
        f"('{t}', {info['price']})" for t, info in TIERS.items()
    )

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT
                bs.referral_source,
                COUNT(*)::int                                AS subscriptions,
                COALESCE(SUM(tp.price_chf), 0)::numeric(12,2) AS mrr_chf
            FROM billing_subscriptions bs
            LEFT JOIN (VALUES {tier_values}) AS tp(tier, price_chf)
              ON tp.tier = bs.tier
            WHERE bs.active = true
              AND bs.referral_source IS NOT NULL
            GROUP BY bs.referral_source
            ORDER BY mrr_chf DESC, bs.referral_source
        """)

    return {
        "referrals": [
            {
                "referral_source": r["referral_source"],
                "subscriptions": r["subscriptions"],
                "mrr_chf": float(r["mrr_chf"]),
            }
            for r in rows
        ],
        "total_sources": len(rows),
        "total_mrr_chf": float(sum(r["mrr_chf"] for r in rows)),
    }
