#!/usr/bin/env python3
"""Produce a Stripe TEST-mode reseller invoice + fetch its PDF (Nachweis).

Safe by construction: uses STRIPE_SECRET_KEY_TEST only and refuses a live key
(see app/reseller_invoice._resolve_key). Never sends the invoice.

Usage:
  set -a; source ~/.moltrust_secrets; set +a   # needs STRIPE_SECRET_KEY_TEST + DB creds
  python3 scripts/reseller_test_invoice.py --login ownify
  # or drive it without the DB:
  python3 scripts/reseller_test_invoice.py --count 12 --price-eur 4.00 --email billing@ownify.example
"""
import argparse
import asyncio
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.reseller_invoice import create_reseller_invoice  # noqa: E402


async def _lookup(login):
    conn = await asyncpg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "moltstack"),
        user="moltstack",
        password=os.getenv("MOLTSTACK_DB_PW"),
    )
    try:
        row = await conn.fetchrow(
            "SELECT r.payer_ref, r.display_name, r.wholesale_price_cents, r.customer_vat_id, "
            "a.email, a.stripe_customer_id "
            "FROM reseller_accounts r JOIN accounts a ON a.payer_ref = r.payer_ref "
            "WHERE r.login = $1",
            login.strip().lower(),
        )
        if not row:
            raise SystemExit(f"no reseller with login={login}")
        count = await conn.fetchval("SELECT count(*) FROM agent_payer WHERE payer_ref = $1", row["payer_ref"]) or 0
        return dict(row), int(count)
    finally:
        await conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--login", default=None, help="resolve count/price/customer from the DB")
    p.add_argument("--count", type=int, default=None)
    p.add_argument("--price-eur", type=float, default=None)
    p.add_argument("--email", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--vat-id", default=None,
                   help="recipient USt-IdNr; without it the invoice stays draft (no PDF)")
    p.add_argument("--no-finalize", action="store_true", help="leave as draft (no PDF)")
    args = p.parse_args()

    if args.login:
        row, count = asyncio.run(_lookup(args.login))
        price_cents = int(row["wholesale_price_cents"])
        email, name = row["email"], row["display_name"]
        customer_id = row["stripe_customer_id"]
        vat_id = args.vat_id or row["customer_vat_id"]
    else:
        if args.count is None or args.price_eur is None:
            raise SystemExit("provide --login, or both --count and --price-eur")
        count, price_cents = args.count, int(round(args.price_eur * 100))
        email, name, customer_id = args.email, args.name, None
        # Placeholder USt-IdNr so the Nachweis run can finalize and yield a PDF.
        vat_id = args.vat_id or "DE-PLATZHALTER-USTID"

    result = create_reseller_invoice(
        count=count, wholesale_price_cents=price_cents, currency="eur",
        email=email, display_name=name, existing_customer_id=customer_id,
        recipient_vat_id=vat_id, finalize=not args.no_finalize,
    )
    print("=== reseller test invoice (Stripe TEST mode, NET / reverse charge) ===")
    for k in ("id", "status", "customer", "currency", "recipient_vat_id",
              "total", "finalized", "finalize_reason", "hosted_invoice_url", "invoice_pdf"):
        print(f"{k:20}: {result.get(k)}")


if __name__ == "__main__":
    main()
