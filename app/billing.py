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

import json
import os
import secrets
import logging
import re
import time
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from typing import Optional

from app.credits import grant_credits, ensure_balance_row
from app.free_tier import FREE_CALLS_PER_HOUR, FREE_MONTHLY_FLOOR

REF_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# ── Stripe init ─────────────────────────────────────────────────────────────
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

# Fail-fast: an empty webhook secret does not disable signature checking, it
# makes the signature forgeable. stripe.Webhook.construct_event would happily
# validate against "" — and this code is public, so anyone could compute the
# HMAC for an empty key and post their own checkout.session.completed to hand
# themselves credits or a subscription. Refuse to start instead, the same way
# MOLTRUST_API_KEYS / NONCE_SECRET / MOLTSTACK_DB_PW already do.
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if not WEBHOOK_SECRET:
    raise RuntimeError(
        "STRIPE_WEBHOOK_SECRET environment variable is required — an empty "
        "secret makes webhook signatures forgeable, not absent"
    )

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
# The v2 catalogue is USD-only: every lookup_key below resolves to a USD price,
# and the one live EUR price carries no lookup_key, so an EUR checkout could only
# ever end in the 400 from the price lookup further down. Advertising "eur" in
# /plans told integrators otherwise and sent them into that dead end.
#
# Restoring EUR is a Stripe-catalogue change, not a code change: give the EUR
# prices lookup_keys, then add "eur" back here.
SUPPORTED_CURRENCIES = ("usd",)

SLOT_LOOKUP_KEY = "mt_v2_slot_monthly"  # load-bearing binding for the $9 add-on slot; price resolved live by lookup_key, not a hardcoded price_id

# Not a tier: no price, no lookup_key, nothing to check out. It is here so that
# a machine reading /billing/plans learns the same boundary the pricing page
# draws — finding out about your own skills is free, proving something to
# someone else is paid.
FREE_TIER = {
    "name": "MolTrust Free",
    "price": 0,
    "signup": ["email", "did_signature"],
    "boundary": "Self-use is free: audit, verify and resolve your own agents and "
                "skills. Paid is gate-use: a signed credential you show a third party.",
    "free_calls_per_hour": FREE_CALLS_PER_HOUR,
    "monthly_credit_floor": FREE_MONTHLY_FLOOR,
    "floor_stacks": False,
    "first_credential_issuance_free": True,
    "anonymous_calls": "unchanged — no DID, no hourly allowance",
    "retention_months": 0,
}

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
    # MolTrust Ads Pro: one seat per Amazon ad account, quantity = ad accounts.
    # Bought with an API key (X-API-Key), so the subscription binds to that key's
    # payer_ref; app/ads_plan.py reads the quantity at the /enforce boundary.
    "ads_pro": {
        "name": "MolTrust Ads Pro",
        "price": 49,
        "unit": "ad account",
        "min_quantity": 1,
        "max_quantity": 100,
        "verdicts_per_month": None,
        "lookup_key": "mt_ads_pro_monthly",
        "requires": "X-API-Key",
    },
}

ADS_PRO = "ads_pro"
ADS_SUCCESS_URL = "https://moltrust.ch/ads/thanks.html?session_id={CHECKOUT_SESSION_ID}"
ADS_CANCEL_URL = "https://moltrust.ch/ads/pricing.html?checkout=cancelled"
PORTAL_DEFAULT_RETURN = "https://moltrust.ch/ads/"
RETURN_URL_PREFIXES = ("https://moltrust.ch/", "https://www.moltrust.ch/")

# Events the webhook books. scripts/check_stripe_webhook_events.py (invariant
# c-stripe-webhook-events) checks the live endpoint is subscribed to all of them.
HANDLED_EVENTS = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
)
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
    # Seats bought (Ads Pro: ad accounts). billing_subscriptions is moltstack-owned.
    await conn.execute("""
        ALTER TABLE billing_subscriptions
        ADD COLUMN IF NOT EXISTS quantity INTEGER
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
    quantity: int = Field(
        default=1, ge=1, le=100,
        description="Seats. ads_pro: number of Amazon ad accounts (1..100); the buyer can "
                    "still adjust it on the Stripe page. Every other tier: 1.",
    )
    ref: Optional[str] = Field(
        default=None,
        description="Optional referral source tag (e.g. 'dsncon'). Stored on the subscription for attribution.",
    )
    success_url: str = "https://moltrust.ch/billing/success"
    cancel_url: str = "https://moltrust.ch/billing/cancel"
class PortalRequest(BaseModel):
    customer_id: Optional[str] = Field(
        default=None,
        description="Optional. If given it must be the customer bound to the API key.",
    )
    return_url: Optional[str] = Field(
        default=None,
        description=f"Where the portal's back link goes; must start with https://moltrust.ch/. "
                    f"Default {PORTAL_DEFAULT_RETURN}",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH  —  an API key, and the Stripe customer bound to its payer_ref
# ═══════════════════════════════════════════════════════════════════════════════

def _require_key(x_api_key: Optional[str]) -> str:
    """401 without a key, 403 for an unknown one (app.main.verify_api_key)."""
    from app.main import verify_api_key
    return verify_api_key(x_api_key)


def _pool():
    from app.main import db_pool
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    return db_pool


async def _account_for_key(conn, api_key: str):
    """(payer_ref, stripe_customer_id, email) for the key; payer_ref None for a key
    that has no account (env keys, keys from before 2026-07)."""
    row = await conn.fetchrow(
        "SELECT k.payer_ref, a.stripe_customer_id, COALESCE(a.email, k.email) AS email "
        "FROM api_keys k LEFT JOIN accounts a ON a.payer_ref = k.payer_ref "
        "WHERE k.key = $1",
        api_key,
    )
    if not row:
        return None, None, None
    return row["payer_ref"], row["stripe_customer_id"], row["email"]


def _no_customer() -> JSONResponse:
    return JSONResponse(status_code=404, content={
        "error": "no_billing_customer",
        "message": "No Stripe customer is bound to this API key yet. It is bound when "
                   "a subscription is bought with this key: POST /billing/checkout "
                   "with X-API-Key.",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/plans")
async def list_plans():
    """Public: return all available plans, free tier included.

    The free tier is described here but kept out of TIERS: everything in that
    dict resolves to a Stripe lookup_key, and a checkout for "free" has nothing
    to resolve. Listing it only on the pricing page while /plans showed three
    paid tiers left every machine reader believing there was no free tier.
    """
    return {
        "plans": TIERS,
        "free": FREE_TIER,
        "currencies": list(SUPPORTED_CURRENCIES),
    }
@router.post("/checkout")
async def create_checkout(
    req: CheckoutRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Create a Stripe Checkout Session.
    Returns {checkout_url} — redirect customer there.

    `ads_pro` needs X-API-Key: the subscription binds to that key's payer_ref, and
    the ads plan is read from it. Its success and cancel URLs are fixed server-side.
    """
    if req.tier not in TIERS:
        raise HTTPException(400, f"Unknown tier: {req.tier}. Use: {list(TIERS)}")
    if req.tier == ADS_PRO:
        return await _ads_checkout(req, x_api_key)
    if req.quantity != 1:
        raise HTTPException(400, f"quantity applies to {ADS_PRO} only; {req.tier} is bought one at a time.")

    currency = (req.currency or "").lower()
    if currency not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            400,
            f"Unsupported currency: {req.currency}. "
            f"Use one of: {', '.join(SUPPORTED_CURRENCIES)}.",
        )

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
                existing_meta = existing.to_dict().get("metadata") or {}
                if not existing_meta.get("referral_source"):
                    stripe.Customer.modify(
                        existing.id,
                        metadata={**existing_meta, "referral_source": ref},
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
# ── Stripe Tax ────────────────────────────────────────────────────────────────
# automatic_tax is switched on for ads_pro only when the account can actually
# compute tax: tax settings `active` and a tax behaviour known for the price
# (on the price or as the account default). Anything else leaves it off; a
# checkout that Stripe rejects for missing tax setup would cost the sale.
_TAX_CACHE: dict = {"at": 0.0, "ready": False, "status": None}
_TAX_TTL = 600


def _tax_ready(price) -> bool:
    now = time.time()
    if now - _TAX_CACHE["at"] > _TAX_TTL:
        try:
            ts = stripe.tax.Settings.retrieve()
            _TAX_CACHE.update(
                at=now, status=ts.status,
                default_behavior=(ts.defaults.tax_behavior if ts.defaults else None))
        except Exception as e:  # noqa: BLE001 - unknown tax state = no automatic tax
            logger.warning("Stripe tax settings unreadable: %s", type(e).__name__)
            _TAX_CACHE.update(at=now, status=None, default_behavior=None)
    behavior = getattr(price, "tax_behavior", None)
    if behavior in (None, "unspecified"):
        behavior = _TAX_CACHE.get("default_behavior")
    return _TAX_CACHE["status"] == "active" and behavior in ("inclusive", "exclusive")


async def _ads_checkout(req: CheckoutRequest, x_api_key: Optional[str]):
    api_key = _require_key(x_api_key)
    if (req.currency or "").lower() != "usd":
        raise HTTPException(400, "ads_pro is priced in USD only.")
    ref = (req.ref or "").strip().lower()
    if ref and not REF_RE.match(ref):
        raise HTTPException(400, "Invalid ref: use 1–64 chars of [a-z0-9_-]")

    lk = TIERS[ADS_PRO]["lookup_key"]
    pl = stripe.Price.list(lookup_keys=[lk], active=True, limit=1)
    price = pl.data[0] if pl.data else None
    if price is None:
        raise HTTPException(500, f"No active Stripe price for tier '{ADS_PRO}' (lookup_key={lk}).")

    from app import accounts as _accounts
    async with _pool().acquire() as conn:
        payer_ref, customer_id, email = await _account_for_key(conn, api_key)
        if payer_ref is None:
            # Env keys and keys from before the payer edge: mint the account now.
            payer_ref = await _accounts.create_account_for_key(conn, api_key, req.email or email)
            customer_id = None
        existing = await conn.fetchval(
            "SELECT stripe_subscription_id FROM billing_subscriptions "
            "WHERE payer_ref = $1 AND tier = $2 AND active = true LIMIT 1",
            payer_ref, ADS_PRO)
        if existing:
            return JSONResponse(status_code=409, content={
                "error": "ads_pro_already_active",
                "message": "This API key already has an active Ads Pro subscription. "
                           "Change the number of ad accounts or cancel in the customer "
                           "portal: POST /billing/portal with X-API-Key.",
            })
        if not customer_id:
            # Never look the customer up by email: the email on a key is not
            # verified, and binding to a stranger's customer would hand over
            # their portal.
            cust = stripe.Customer.create(
                email=req.email or email or None,
                metadata={"payer_ref": payer_ref, "product": "ads",
                          **({"referral_source": ref} if ref else {})},
            )
            customer_id = cust.id
            await _accounts.bind_stripe_customer(conn, payer_ref, customer_id)

    meta = {"tier": ADS_PRO, "payer_ref": payer_ref, "agent_did": req.agent_did or ""}
    if ref:
        meta["referral_source"] = ref
    kwargs = dict(
        customer=customer_id,
        mode="subscription",
        currency="usd",
        line_items=[{
            "price": price.id,
            "quantity": req.quantity,
            "adjustable_quantity": {"enabled": True, "minimum": 1, "maximum": 100},
        }],
        success_url=ADS_SUCCESS_URL,
        cancel_url=ADS_CANCEL_URL,
        metadata=meta,
        subscription_data={"metadata": meta},
        # Business buyers: billing address is required and a VAT/tax ID can be entered on
        # every Ads Pro checkout, whether or not Stripe Tax is active yet. Both are written
        # back to the customer, which Checkout requires once a customer is passed in.
        billing_address_collection="required",
        tax_id_collection={"enabled": True},
        customer_update={"address": "auto", "name": "auto"},
    )
    tax = _tax_ready(price)
    if tax:
        kwargs["automatic_tax"] = {"enabled": True}
    session = stripe.checkout.Session.create(**kwargs)
    logger.info("Ads checkout created: %s qty=%s tax=%s ref=%s",
                session.id, req.quantity, tax, ref or "-")
    return {"checkout_url": session.url, "session_id": session.id,
            "tier": ADS_PRO, "quantity": req.quantity, "automatic_tax": tax}


@router.post("/portal")
async def customer_portal(
    req: PortalRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Stripe Customer Portal session for the customer bound to the API key.

    Requires X-API-Key. Serves only the Stripe customer bound to the key's
    payer_ref (accounts.stripe_customer_id); a customer_id in the body that is
    not that customer is refused. Until 12.10.2026 this took any customer_id
    without authentication (invariant c-billing-refuses-anonymous).
    """
    api_key = _require_key(x_api_key)
    return_url = req.return_url or PORTAL_DEFAULT_RETURN
    if not return_url.startswith(RETURN_URL_PREFIXES):
        raise HTTPException(400, "return_url must start with https://moltrust.ch/")
    async with _pool().acquire() as conn:
        _payer_ref, customer_id, _email = await _account_for_key(conn, api_key)
    if not customer_id:
        return _no_customer()
    if req.customer_id and req.customer_id != customer_id:
        raise HTTPException(403, "customer_id is not the customer bound to this API key")
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return {"portal_url": portal.url}


def _sub_period_end(sub: dict):
    """current_period_end: on the subscription before API 2025-03-31, on its items since."""
    ts = sub.get("current_period_end")
    if ts is None:
        items = (sub.get("items") or {}).get("data") or []
        ends = [i.get("current_period_end") for i in items if i.get("current_period_end")]
        ts = max(ends) if ends else None
    return ts


def _sub_quantity(sub: dict) -> int:
    items = (sub.get("items") or {}).get("data") or []
    q = sum(int(i.get("quantity") or 0) for i in items)
    return q or 1


@router.get("/subscription/{customer_id}")
async def get_subscription(
    customer_id: str,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """Current subscription of the customer bound to the API key.

    Requires X-API-Key; customer_id must be the customer bound to the key's
    payer_ref, anything else is 403. Until 12.10.2026 this answered anyone.
    """
    api_key = _require_key(x_api_key)
    async with _pool().acquire() as conn:
        _payer_ref, bound, _email = await _account_for_key(conn, api_key)
    if not bound:
        return _no_customer()
    if customer_id != bound:
        raise HTTPException(403, "customer_id is not the customer bound to this API key")
    subs = stripe.Subscription.list(customer=customer_id, status="active", limit=1)
    if not subs.data:
        return {"active": False, "tier": None}

    sub = subs.data[0].to_dict()
    tier = (sub.get("metadata") or {}).get("tier", "unknown")
    end = _sub_period_end(sub)
    return {
        "active": True,
        "tier": tier,
        "tier_info": TIERS.get(tier),
        "quantity": _sub_quantity(sub),
        "current_period_end": datetime.fromtimestamp(end, tz=timezone.utc).isoformat()
        if end else None,
        "cancel_at_period_end": sub.get("cancel_at_period_end", False),
        "stripe_subscription_id": sub["id"],
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

    # The signature is verified; read the payload as plain JSON. Stripe objects
    # are not dicts since stripe-python 12, and every `.get()` below would raise.
    raw_event = json.loads(payload)
    event_type = raw_event["type"]
    data = raw_event["data"]["object"]

    logger.info("Stripe webhook: %s id=%s", event_type, raw_event["id"])

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
            referral_source = (customer.to_dict().get("metadata") or {}).get("referral_source") or None
        except Exception as e:
            logger.warning("Could not fetch customer %s for referral lookup: %s", sub.get("customer"), e)

    # A subscription Stripe reports as ended is not active, whatever the event.
    if sub.get("status") in ("canceled", "incomplete_expired", "unpaid"):
        active = False
    await conn.execute("""
        INSERT INTO billing_subscriptions
            (stripe_subscription_id, stripe_customer_id, tier, agent_did,
             payer_ref, active, current_period_end, cancel_at_period_end,
             referral_source, quantity, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, to_timestamp($7), $8, $9, $10, NOW())
        ON CONFLICT (stripe_subscription_id) DO UPDATE SET
            tier                 = EXCLUDED.tier,
            payer_ref            = COALESCE(billing_subscriptions.payer_ref, EXCLUDED.payer_ref),
            active               = EXCLUDED.active,
            current_period_end   = EXCLUDED.current_period_end,
            cancel_at_period_end = EXCLUDED.cancel_at_period_end,
            referral_source      = COALESCE(billing_subscriptions.referral_source, EXCLUDED.referral_source),
            quantity             = EXCLUDED.quantity,
            updated_at           = NOW()
    """,
        sub["id"],
        sub["customer"],
        tier,
        agent_did,
        payer_ref,
        active,
        _sub_period_end(sub),
        sub.get("cancel_at_period_end", False),
        referral_source,
        _sub_quantity(sub),
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
    meta = sub.to_dict().get("metadata") or {}
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
        """)  # nosec B608 - the interpolated VALUES list is built from the TIERS module constant; no input reaches the SQL

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
