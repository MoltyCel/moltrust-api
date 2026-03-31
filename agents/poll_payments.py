#!/usr/bin/env python3
"""
Hourly USDC payment poller for MoltGuard wallet on Base.
Checks Basescan API for incoming USDC transfers and saves to payment_events.
Cron: 0 * * * * (hourly)
"""
import os, sys, asyncio, json, logging
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.parse import urlencode

WALLET = "0x380238347e58435f40B4da1F1A045A271D5838F5"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASESCAN_KEY = os.environ.get("BASESCAN_API_KEY", "")
BASESCAN_URL = "https://api.basescan.org/api"
DB_USER = "moltstack"
DB_NAME = "moltstack"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("poll_payments")


def fetch_recent_transfers() -> list:
    """Fetch last 50 incoming USDC transfers to the wallet via Basescan API."""
    if not BASESCAN_KEY:
        log.error("BASESCAN_API_KEY not set")
        return []

    params = urlencode({
        "module": "account",
        "action": "tokentx",
        "address": WALLET,
        "contractaddress": USDC_BASE,
        "sort": "desc",
        "offset": "50",
        "page": "1",
        "apikey": BASESCAN_KEY,
    })
    url = f"{BASESCAN_URL}?{params}"

    try:
        req = Request(url, headers={"User-Agent": "MolTrust/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.error("Basescan API error: %s", e)
        return []

    if data.get("status") != "1":
        msg = data.get("message", "unknown")
        if msg != "No transactions found":
            log.warning("Basescan response: %s", msg)
        return []

    # Only incoming transfers
    return [
        t for t in data.get("result", [])
        if t.get("to", "").lower() == WALLET.lower()
    ]


async def main():
    import asyncpg

    log.info("Polling USDC transfers to %s...", WALLET[:10])

    conn = await asyncpg.connect(user=DB_USER, database=DB_NAME)

    try:
        transfers = fetch_recent_transfers()
        if not transfers:
            log.info("No incoming transfers found.")
            return

        # Get already-known tx hashes
        rows = await conn.fetch("SELECT tx_hash FROM payment_events")
        known = {r["tx_hash"] for r in rows}

        new_count = 0
        for t in transfers:
            tx_hash = t.get("hash", "")
            if not tx_hash or tx_hash in known:
                continue

            amount_usdc = int(t.get("value", 0)) / 1_000_000
            from_addr = t.get("from", "")[:64]
            to_addr = t.get("to", "")[:64]
            ts = int(t.get("timeStamp", 0))

            # DID reverse-lookup
            did = await conn.fetchval(
                "SELECT did FROM agents WHERE LOWER(wallet_address) = LOWER($1)",
                to_addr
            )

            await conn.execute("""
                INSERT INTO payment_events
                    (tx_hash, from_address, to_address, amount_usdc, token, did, received_at)
                VALUES ($1, $2, $3, $4, 'USDC', $5, to_timestamp($6))
                ON CONFLICT (tx_hash) DO NOTHING
            """, tx_hash, from_addr, to_addr, amount_usdc, did, ts)

            new_count += 1
            log.info("New: %.2f USDC from %s TX:%s", amount_usdc, from_addr[:10], tx_hash[:16])

        log.info("Done. %d new payment(s) saved.", new_count)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
