#!/usr/bin/env python3
"""MolTrust Ads Pro in Stripe: product, price, webhook events. Idempotent.

    python3 scripts/stripe_setup_ads.py --mode test            # dry run, test mode
    python3 scripts/stripe_setup_ads.py --mode test --apply    # create what is missing
    python3 scripts/stripe_setup_ads.py --mode live --apply

Product "MolTrust Ads Pro", price USD 49.00 per month per ad account (licensed,
quantity = ad accounts), lookup_key mt_ads_pro_monthly. The price is found by
lookup_key, never created twice. No tax_behavior is set on the price: that is a
tax decision and stays with the account's defaults.

With --webhook-events the live-mode webhook https://api.moltrust.ch/billing/webhook
is subscribed to every event app/billing.py books (adds, never removes). The tax
settings status is printed read-only; this script never changes tax settings.

Keys: --mode test reads STRIPE_SECRET_KEY_TEST, --mode live STRIPE_SECRET_KEY.
Prints IDs and statuses only, never key material.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

LOOKUP_KEY = "mt_ads_pro_monthly"
PRODUCT_NAME = "MolTrust Ads Pro"
PRODUCT_DESCRIPTION = ("Verdicts for Amazon Ads agents: allow, hold or forbid per tool call. "
                       "USD 49 per ad account per month, cancel monthly.")
UNIT_AMOUNT = 4900
WEBHOOK_URL = "https://api.moltrust.ch/billing/webhook"
HANDLED_EVENTS = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
)


def _list_all(lister, **params):
    out, after = [], None
    for _ in range(50):
        page = lister(limit=100, **({"starting_after": after} if after else {}), **params)
        out.extend(page.data)
        if not page.has_more:
            return out
        after = page.data[-1].id
    raise RuntimeError("more than 50 pages")


def ensure_product(stripe, apply):
    for p in _list_all(stripe.Product.list, active=True):
        if p.name == PRODUCT_NAME:
            return {"id": p.id, "action": "exists"}
    if not apply:
        return {"id": None, "action": "would-create"}
    p = stripe.Product.create(name=PRODUCT_NAME, description=PRODUCT_DESCRIPTION,
                              unit_label="ad account",
                              metadata={"tier": "ads_pro", "unit": "ad_account"})
    return {"id": p.id, "action": "created"}


def ensure_price(stripe, product_id, apply):
    found = stripe.Price.list(lookup_keys=[LOOKUP_KEY], limit=1).data
    if found:
        p = found[0]
        ok = (p.unit_amount == UNIT_AMOUNT and p.currency == "usd"
              and p.recurring and p.recurring.interval == "month"
              and p.recurring.usage_type == "licensed")
        return {"id": p.id, "action": "exists", "active": p.active,
                "matches_spec": bool(ok), "tax_behavior": p.tax_behavior}
    if not apply or not product_id:
        return {"id": None, "action": "would-create"}
    p = stripe.Price.create(product=product_id, currency="usd", unit_amount=UNIT_AMOUNT,
                            recurring={"interval": "month", "usage_type": "licensed"},
                            lookup_key=LOOKUP_KEY, nickname="Ads Pro monthly per ad account",
                            metadata={"tier": "ads_pro"})
    return {"id": p.id, "action": "created", "active": p.active, "matches_spec": True,
            "tax_behavior": p.tax_behavior}


def ensure_webhook_events(stripe, apply):
    eps = [w for w in _list_all(stripe.WebhookEndpoint.list) if w.url == WEBHOOK_URL]
    if not eps:
        return {"action": "missing-endpoint"}
    w = eps[0]
    have = list(w.enabled_events or [])
    missing = [e for e in HANDLED_EVENTS if e not in have and "*" not in have]
    if not missing:
        return {"id": w.id, "action": "ok", "enabled_events": sorted(have)}
    if not apply:
        return {"id": w.id, "action": "would-add", "missing": missing}
    w = stripe.WebhookEndpoint.modify(w.id, enabled_events=sorted(set(have) | set(missing)))
    return {"id": w.id, "action": "added", "added": missing,
            "enabled_events": sorted(w.enabled_events)}


def tax_status(stripe):
    try:
        ts = stripe.tax.Settings.retrieve()
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreadable", "error": type(exc).__name__}
    d = ts.to_dict()
    regs = _list_all(stripe.tax.Registration.list)
    return {"status": d.get("status"), "status_details": d.get("status_details"),
            "head_office_set": bool(d.get("head_office")),
            "default_tax_behavior": (d.get("defaults") or {}).get("tax_behavior"),
            "registrations": [(r.country, r.status) for r in regs]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("test", "live"), required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--webhook-events", action="store_true",
                    help="subscribe the billing webhook to the events billing.py books")
    args = ap.parse_args()
    var = "STRIPE_SECRET_KEY_TEST" if args.mode == "test" else "STRIPE_SECRET_KEY"
    key = os.environ.get(var, "")
    expected = "_test_" if args.mode == "test" else "_live_"
    if not key or expected not in key[:9]:
        print(f"ERROR: {var} not set or not a {args.mode}-mode key", file=sys.stderr)
        return 2
    import stripe
    stripe.api_key = key
    out = {"mode": args.mode, "apply": args.apply}
    prod = ensure_product(stripe, args.apply)
    out["product"] = prod
    out["price"] = ensure_price(stripe, prod["id"], args.apply)
    if args.webhook_events:
        out["webhook"] = ensure_webhook_events(stripe, args.apply)
    out["tax"] = tax_status(stripe)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
