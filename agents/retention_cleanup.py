#!/usr/bin/env python3
"""DSGVO Retention: delete request_log entries older than 30 days. Daily cron.

Before pruning, every external IP MIN(ts) is frozen into the known_callers
ledger (ON CONFLICT DO NOTHING) so retention can never strip an IP history
and let it re-float as "truly new" in traffic_monitor.
"""
import asyncio, logging, os
from urllib.request import Request, urlopen
import json

from app import notify

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("retention")

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram(msg):
    if not notify.telegram_allowed("retention_cleanup.send_telegram", logger=log):
        return
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        data = json.dumps({"chat_id": TG_CHAT, "text": msg}).encode()
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        if not url.startswith(("http://", "https://")):
            return
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)  # noqa: S310 — scheme validated above  # nosec B310 - host is the literal Telegram API, only the bot token comes from env
    except Exception:
        pass


async def main():
    import asyncpg
    conn = await asyncpg.connect(user="moltstack", database="moltstack")
    try:
        # Freeze every external IP true first_seen into the known_callers ledger
        # BEFORE pruning, so retention can never strip an IP history and let it
        # re-float as "truly new". ON CONFLICT DO NOTHING keeps curated/existing
        # rows. Trusted-prefix list mirrors traffic_monitor.TRUSTED_PREFIXES.
        backfill = await conn.execute("""
            INSERT INTO known_callers (ip, first_seen, label, category)
            SELECT ip, MIN(ts), NULL, 'auto'
            FROM request_log
            WHERE ip IS NOT NULL
              AND ip NOT LIKE '127.%'     AND ip <> '::1'
              AND ip NOT LIKE '10.%'      AND ip NOT LIKE '172.16.%'
              AND ip NOT LIKE '192.168.%' AND ip NOT LIKE '88.99.%'
              AND ip NOT LIKE '116.202.%' AND ip NOT LIKE '46.225.175.%'
            GROUP BY ip
            ON CONFLICT (ip) DO NOTHING
        """)
        backfilled = int(backfill.split()[-1]) if backfill else 0
        log.info("Ledger backfill: %d new IP(s) frozen before pruning", backfilled)

        result = await conn.execute("DELETE FROM request_log WHERE ts < NOW() - INTERVAL '30 days'")
        deleted = int(result.split()[-1]) if result else 0
        log.info("Deleted %d old request_log entries", deleted)
        if deleted > 0:
            send_telegram(f"DSGVO Retention: {deleted} request_log entries deleted (>30 days)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
