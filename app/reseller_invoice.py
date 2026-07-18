"""Reseller B2B invoicing — Stripe Invoicing API (Phase 2, 2026-07-18).

Monthly reseller invoice: N agents x wholesale_price (EUR). This is the Stripe
Invoicing API (Invoice / InvoiceItem), NOT Checkout/Subscription — that path is
net-new for this repo (Phase-1 finding).

SAFETY GATES (per brief — build & test, do NOT scharf-schalten):
  * Key source is STRIPE_SECRET_KEY_TEST, a SEPARATE test-mode key. This module
    REFUSES to run against a live key (sk_live...) unless RESELLER_INVOICE_LIVE=1
    is explicitly set — so a test run can never emit a real invoice to a customer.
  * The invoice is created + finalized in TEST mode only (test invoices carry no
    real charge) to produce a retrievable PDF for the Nachweis. It is never sent
    (`send_invoice` collection method, auto_advance off) — no email goes out.
  * Live faktura to Ownify waits on the confirmed tax treatment (reverse charge
    CH->DE) — a separate track. That is why live mode is flag-gated OFF here.
"""
import os
import logging

log = logging.getLogger("reseller_invoice")


def _resolve_key() -> str:
    """Return the Stripe key to use, enforcing the test-only gate."""
    live_ok = os.getenv("RESELLER_INVOICE_LIVE") == "1"
    key = os.getenv("STRIPE_SECRET_KEY_TEST", "")
    if not key and live_ok:
        # Only in an explicit live run do we fall back to the primary key.
        key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise RuntimeError(
            "STRIPE_SECRET_KEY_TEST is not set. Add a Stripe test-mode key to "
            "~/.moltrust_secrets to build/test reseller invoicing safely."
        )
    if key.startswith("sk_live") and not live_ok:
        raise RuntimeError(
            "Refusing to create a reseller invoice with a LIVE Stripe key. "
            "Reseller invoicing is test-only until tax treatment is confirmed; "
            "set RESELLER_INVOICE_LIVE=1 to override (do NOT do this before "
            "the tax sign-off)."
        )
    return key


def _find_or_create_customer(stripe, *, email, display_name, existing_customer_id):
    if existing_customer_id:
        try:
            c = stripe.Customer.retrieve(existing_customer_id)
            if not getattr(c, "deleted", False):
                return c.id
        except Exception:  # noqa: BLE001 — fall through to (re)create
            pass
    if email:
        found = stripe.Customer.list(email=email, limit=1)
        if found.data:
            return found.data[0].id
    c = stripe.Customer.create(
        email=email,
        name=display_name,
        description=f"MolTrust reseller {display_name or email or ''}".strip(),
    )
    return c.id


def create_reseller_invoice(*, count, wholesale_price_cents, currency="eur",
                            email=None, display_name=None, existing_customer_id=None,
                            days_until_due=14, finalize=True):
    """Create a draft (optionally finalized) reseller invoice in Stripe TEST mode.

    Returns a dict with id, status, total, hosted_invoice_url, invoice_pdf,
    customer. Requires the `stripe` package + STRIPE_SECRET_KEY_TEST. Never sends
    the invoice; finalize=True only produces the PDF (test invoice = no charge).
    """
    import stripe
    stripe.api_key = _resolve_key()
    currency = (currency or "eur").lower()
    if currency != "eur":
        raise ValueError("reseller invoicing is pinned to EUR")
    if count < 0 or wholesale_price_cents < 0:
        raise ValueError("count and price must be non-negative")

    customer_id = _find_or_create_customer(
        stripe, email=email, display_name=display_name,
        existing_customer_id=existing_customer_id,
    )

    stripe.InvoiceItem.create(
        customer=customer_id,
        currency=currency,
        unit_amount=wholesale_price_cents,
        quantity=max(count, 0),
        description=f"MolTrust agent slots (wholesale) x{count}",
    )
    invoice = stripe.Invoice.create(
        customer=customer_id,
        collection_method="send_invoice",
        days_until_due=days_until_due,
        auto_advance=False,            # never auto-progress/charge
        pending_invoice_items_behavior="include",
        description="MolTrust reseller monthly invoice (wholesale)",
        metadata={"kind": "reseller_wholesale"},
    )
    result = {
        "id": invoice.id,
        "status": invoice.status,
        "customer": customer_id,
        "currency": currency,
        "total": getattr(invoice, "total", None),
        "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
        "invoice_pdf": getattr(invoice, "invoice_pdf", None),
    }
    if finalize:
        final = stripe.Invoice.finalize_invoice(invoice.id, auto_advance=False)
        result.update({
            "status": final.status,
            "total": getattr(final, "total", None),
            "hosted_invoice_url": getattr(final, "hosted_invoice_url", None),
            "invoice_pdf": getattr(final, "invoice_pdf", None),
        })
    log.info("reseller invoice %s status=%s total=%s", result["id"], result["status"], result["total"])
    return result
