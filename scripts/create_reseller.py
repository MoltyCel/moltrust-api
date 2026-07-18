#!/usr/bin/env python3
"""Manually create a reseller (never self-service). Ops tool.

Usage:
  source ~/.moltrust_secrets  # for DB creds
  python3 scripts/create_reseller.py --login ownify --price-eur 4.00 \
      --name "Ownify" --email billing@ownify.example [--payer-ref pyr_...]

The password is read from the RESELLER_PW env var or prompted (getpass) — never
passed on the command line, never printed, never logged.
"""
import argparse
import asyncio
import getpass
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.reseller import create_reseller, ensure_reseller_tables  # noqa: E402


async def _run(args, password):
    conn = await asyncpg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "moltstack"),
        user="moltstack",
        password=os.getenv("MOLTSTACK_DB_PW"),
    )
    try:
        await ensure_reseller_tables(conn)
        payer_ref = await create_reseller(
            conn, args.login, password, int(round(args.price_eur * 100)),
            display_name=args.name, email=args.email, payer_ref=args.payer_ref,
        )
        print(f"reseller created: payer_ref={payer_ref} login={args.login.strip().lower()}")
    finally:
        await conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--login", required=True)
    p.add_argument("--price-eur", type=float, required=True, help="wholesale price per agent, EUR (e.g. 4.00)")
    p.add_argument("--name", default=None)
    p.add_argument("--email", default=None)
    p.add_argument("--payer-ref", default=None, help="attach to an existing payer_ref instead of minting one")
    args = p.parse_args()

    password = os.getenv("RESELLER_PW") or getpass.getpass("Reseller password: ")
    if not password:
        print("error: empty password", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_run(args, password))


if __name__ == "__main__":
    main()
