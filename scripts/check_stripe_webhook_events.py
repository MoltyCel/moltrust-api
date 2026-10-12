#!/usr/bin/env python3
"""The live Stripe webhook endpoint subscribes to every event app/billing.py handles.

Prints `WEBHOOK EVENTS OK` or `missing: <events>`; `UNREADABLE: <reason>` when
Stripe cannot be read. Reads STRIPE_SECRET_KEY from the environment and prints
no key material. Pages through every webhook endpoint (has_more) before judging.

Origin (12.10.2026, MolTrust Ads T4): the endpoint https://api.moltrust.ch/billing/webhook
was subscribed to checkout.session.completed and customer.subscription.deleted
only, while the handler books subscriptions from customer.subscription.created /
.updated. A paid subscription would never have reached billing_subscriptions,
and a paid Ads Pro customer would have stayed on the free plan.
"""
from __future__ import annotations

import os
import sys

URL = "https://api.moltrust.ch/billing/webhook"
# Kept equal to app.billing.HANDLED_EVENTS by tests/test_billing_ads.py; not imported,
# because importing app.billing needs the webhook secret in the environment.
HANDLED_EVENTS = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
)


def missing_events(endpoints: list, url: str = URL) -> list | None:
    """Events the endpoint at `url` does not receive; None if there is no such endpoint."""
    mine = [e for e in endpoints if e.get("url") == url and e.get("status") == "enabled"]
    if not mine:
        return None
    enabled = set()
    for e in mine:
        enabled.update(e.get("enabled_events") or [])
    if "*" in enabled:
        return []
    return [ev for ev in HANDLED_EVENTS if ev not in enabled]


def read_endpoints(key: str) -> list:
    import stripe
    out, after = [], None
    for _ in range(50):
        params = {"limit": 100, "api_key": key}
        if after:
            params["starting_after"] = after
        page = stripe.WebhookEndpoint.list(**params)
        rows = [w.to_dict() for w in page.data]
        out.extend(rows)
        if not page.has_more:
            return out
        after = rows[-1]["id"]
    raise RuntimeError("more than 50 pages of webhook endpoints")


def main() -> int:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        print("UNREADABLE: STRIPE_SECRET_KEY not set")
        return 0
    try:
        endpoints = read_endpoints(key)
    except Exception as exc:  # noqa: BLE001 - any failure is "not measured", never "ok"
        print(f"UNREADABLE: {type(exc).__name__}")
        return 0
    missing = missing_events(endpoints)
    if missing is None:
        print(f"missing: no enabled webhook endpoint for {URL}")
    elif missing:
        print("missing: " + ", ".join(missing))
    else:
        print("WEBHOOK EVENTS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
