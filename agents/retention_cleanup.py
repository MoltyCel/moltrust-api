#!/usr/bin/env python3
"""DSGVO Retention: delete request_log entries older than 30 days. Daily cron."""
import asyncio, logging, os
from urllib.request import Request, urlopen
import json

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("retention")

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        data = json.dumps({"chat_id": TG_CHAT, "text": msg}).encode()
        req = Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data=data, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)
    except Exception:
        pass


async def main():
    import asyncpg
    conn = await asyncpg.connect(user="moltstack", database="moltstack")
    try:
        result = await conn.execute("DELETE FROM request_log WHERE ts < NOW() - INTERVAL '30 days'")
        deleted = int(result.split()[-1]) if result else 0
        log.info("Deleted %d old request_log entries", deleted)
        if deleted > 0:
            send_telegram(f"DSGVO Retention: {deleted} request_log entries deleted (>30 days)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
