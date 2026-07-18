"""Reseller B2B invoicing — Stripe Invoicing API (Phase 2, 2026-07-18).

Monthly reseller invoice: N agents x wholesale_price, NET, EUR. This is the
Stripe Invoicing API (Invoice / InvoiceItem), NOT Checkout/Subscription — net-new
for this repo (Phase-1 finding).

Tax treatment (confirmed CH->DE B2B reverse charge):
  * Invoice is NET, no Swiss VAT, EUR. No tax rates / automatic_tax applied.
  * A mandatory reverse-charge legal text goes in the invoice footer. It is a
    CONFIGURABLE value (env RESELLER_INVOICE_REVERSE_CHARGE_TEXT), not hardwired.
  * The recipient's VAT id (German USt-IdNr) is a FORMAL requirement and prints
    on the invoice. Without it the invoice STAYS DRAFT — it cannot be finalized.
  * Issuer = CryptoKRI GmbH. Its name/address/support-email render from the
    Stripe ACCOUNT business profile (Dashboard / live key only) — NOT settable
    from this code. The company registration number (CHE UID, shown WITHOUT a
    VAT/MWST suffix, since CryptoKRI is not VAT-registered) prints via an invoice
    custom field. No seller VAT id is put on the invoice.

  The Swiss registration status (CHF 100k threshold) is a Treuhaender question,
  deliberately NOT modelled here — no field, no code branch.

SAFETY GATES (build & test, do NOT scharf-schalten):
  * Key source is STRIPE_SECRET_KEY_TEST, a SEPARATE test-mode key. Refuses a
    live key (sk_live...) unless RESELLER_INVOICE_LIVE=1 is explicitly set.
  * Never sent (`send_invoice` collection method, auto_advance off) — no email.
  * Live faktura waits on lawyer/Treuhaender sign-off — live mode flag-gated OFF.
"""
import os
import logging

log = logging.getLogger("reseller_invoice")

# Mandatory reverse-charge text — English, configurable, tax-exact (both norms).
DEFAULT_REVERSE_CHARGE_TEXT = (
    "Not subject to Swiss VAT (place of supply: Germany, Art. 8 para. 1 Swiss VAT "
    "Act / MWSTG). Reverse charge — VAT liability shifts to the recipient "
    "(§ 13b German VAT Act / UStG)."
)
# Issuer company registration number (CHE UID). Shown WITHOUT a "MWST"/VAT suffix —
# CryptoKRI GmbH is NOT VAT-registered, so no sender VAT id is put on the invoice.
# NOTE: the issuer NAME + ADDRESS + support email render from the Stripe *account*
# business profile (Dashboard / live key only) — not settable from this code.
DEFAULT_COMPANY_REG = "CHE-115.481.407"


def reverse_charge_text() -> str:
    return os.getenv("RESELLER_INVOICE_REVERSE_CHARGE_TEXT", DEFAULT_REVERSE_CHARGE_TEXT)


def company_reg_no() -> str:
    return os.getenv("RESELLER_INVOICE_COMPANY_REG", DEFAULT_COMPANY_REG)


def _finalize_allowed(recipient_vat_id, requested_finalize: bool) -> tuple[bool, str]:
    """Pure gate: an invoice may only be finalized if a USt-IdNr is present.

    Returns (allowed, reason). Kept side-effect-free so it is unit-testable
    without touching Stripe.
    """
    if not requested_finalize:
        return False, "finalize not requested (draft)"
    if not (recipient_vat_id and str(recipient_vat_id).strip()):
        return False, "recipient USt-IdNr missing — invoice stays draft"
    return True, "ok"


def _resolve_key() -> str:
    """Return the Stripe key to use, enforcing the test-only gate."""
    live_ok = os.getenv("RESELLER_INVOICE_LIVE") == "1"
    key = os.getenv("STRIPE_SECRET_KEY_TEST", "")
    if not key and live_ok:
        key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise RuntimeError(
            "STRIPE_SECRET_KEY_TEST is not set. Add a Stripe test-mode key to "
            "~/.moltrust_secrets to build/test reseller invoicing safely."
        )
    if key.startswith("sk_live") and not live_ok:
        raise RuntimeError(
            "Refusing to create a reseller invoice with a LIVE Stripe key. "
            "Reseller invoicing is test-only until tax sign-off; set "
            "RESELLER_INVOICE_LIVE=1 to override (NOT before the sign-off)."
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


def _attach_vat_id(stripe, customer_id, vat_id):
    """Best-effort: attach the recipient USt-IdNr as a Stripe customer tax id so
    it renders on the invoice. Non-fatal if it fails (also carried in custom_fields
    + footer). German VAT ids use Stripe type 'eu_vat'."""
    try:
        existing = stripe.Customer.list_tax_ids(customer_id, limit=20)
        for t in existing.data:
            if getattr(t, "value", None) == vat_id:
                return
        stripe.Customer.create_tax_id(customer_id, type="eu_vat", value=vat_id)
    except Exception as e:  # noqa: BLE001
        log.warning("could not attach vat id (non-fatal): %s", type(e).__name__)


def create_reseller_invoice(*, count, wholesale_price_cents, currency="eur",
                            email=None, display_name=None, existing_customer_id=None,
                            recipient_vat_id=None, days_until_due=14, finalize=True):
    """Create a NET reseller invoice in Stripe TEST mode (reverse charge, EUR).

    Returns a dict with id, status, total, hosted_invoice_url, invoice_pdf,
    customer, finalized, finalize_reason, recipient_vat_id. The invoice is
    finalized (→ PDF) ONLY if a USt-IdNr is present; otherwise it stays draft.
    Never sent.
    """
    import stripe
    stripe.api_key = _resolve_key()
    currency = (currency or "eur").lower()
    if currency != "eur":
        raise ValueError("reseller invoicing is pinned to EUR")
    if count < 0 or wholesale_price_cents < 0:
        raise ValueError("count and price must be non-negative")

    vat_id = (recipient_vat_id or "").strip() or None
    customer_id = _find_or_create_customer(
        stripe, email=email, display_name=display_name,
        existing_customer_id=existing_customer_id,
    )
    if vat_id:
        _attach_vat_id(stripe, customer_id, vat_id)

    # NET line item — no tax_rates, no automatic_tax. total == net.
    # InvoiceItem takes unit_amount_decimal (string cents), not unit_amount (a
    # Price-only field); quantity x unit_amount_decimal gives the itemized total.
    stripe.InvoiceItem.create(
        customer=customer_id,
        currency=currency,
        unit_amount_decimal=str(int(wholesale_price_cents)),
        quantity=max(count, 0),
        description=f"MolTrust agent slots (wholesale, net) x{count}",
    )

    # English throughout. Recipient VAT id + issuer company reg no. (no seller VAT).
    custom_fields = [
        {"name": "VAT ID (recipient)", "value": (vat_id or "— missing —")[:30]},
        {"name": "Company reg. no.", "value": company_reg_no()[:30]},
    ]
    # Footer = reverse-charge text ONLY. The issuer name/address renders from the
    # Stripe account (appears once, at the top) — deliberately NOT repeated here.
    footer = reverse_charge_text()

    invoice = stripe.Invoice.create(
        customer=customer_id,
        collection_method="send_invoice",
        days_until_due=days_until_due,
        auto_advance=False,            # never auto-progress/charge
        pending_invoice_items_behavior="include",
        description="MolTrust reseller monthly invoice (wholesale, net)",
        footer=footer,
        custom_fields=custom_fields,
        metadata={"kind": "reseller_wholesale", "recipient_vat_id": vat_id or ""},
    )
    result = {
        "id": invoice.id,
        "status": invoice.status,
        "customer": customer_id,
        "currency": currency,
        "recipient_vat_id": vat_id,
        "total": getattr(invoice, "total", None),
        "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
        "invoice_pdf": getattr(invoice, "invoice_pdf", None),
    }

    allowed, reason = _finalize_allowed(vat_id, finalize)
    result["finalized"] = allowed
    result["finalize_reason"] = reason
    if allowed:
        final = stripe.Invoice.finalize_invoice(invoice.id, auto_advance=False)
        result.update({
            "status": final.status,
            "total": getattr(final, "total", None),
            "hosted_invoice_url": getattr(final, "hosted_invoice_url", None),
            "invoice_pdf": getattr(final, "invoice_pdf", None),
        })
    log.info("reseller invoice %s status=%s total=%s finalized=%s",
             result["id"], result["status"], result["total"], result["finalized"])
    return result
