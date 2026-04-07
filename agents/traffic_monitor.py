#!/usr/bin/env python3
"""
New External Caller Alert — runs hourly via cron.
Alerts via Telegram when a new IP makes >10 requests in 24h.
"""
import os, json, asyncio, logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("traffic_monitor")

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

TRUSTED_PREFIXES = [
    "127.", "::1", "10.", "172.16.", "192.168.",
    "46.225.175.",  # Our Hetzner server
]

KNOWN_CALLERS = {
    "74.220.48.244": "Render.com Uptime",
    "172.212.171.144": "Upptime Status",
}


def send_telegram(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        log.warning("Telegram not configured")
        return
    try:
        data = json.dumps({"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}).encode()
        req = Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"},
        )
        urlopen(req, timeout=10)
    except Exception as e:
        log.error("Telegram failed: %s", e)


async def main():
    import asyncpg

    log.info("Checking for new external callers...")
    conn = await asyncpg.connect(user="moltstack", database="moltstack")

    try:
        new_callers = await conn.fetch("""
            WITH recent AS (
                SELECT ip, COUNT(*) as calls,
                       MAX(ts) as last_seen,
                       (array_agg(user_agent ORDER BY ts DESC))[1] as ua,
                       (array_agg(ip_org ORDER BY ts DESC))[1] as org
                FROM request_log
                WHERE ts > NOW() - INTERVAL '25 hours'
                  AND ip NOT IN ('127.0.0.1', '::1')
                GROUP BY ip
                HAVING COUNT(*) > 10
            ),
            known AS (
                SELECT DISTINCT ip FROM request_log
                WHERE ts < NOW() - INTERVAL '25 hours'
            )
            SELECT r.ip, r.calls, r.last_seen, r.ua, r.org
            FROM recent r
            LEFT JOIN known k ON k.ip = r.ip
            WHERE k.ip IS NULL
        """)

        # Filter out trusted prefixes
        new_callers = [
            c for c in new_callers
            if not any(c["ip"].startswith(p) for p in TRUSTED_PREFIXES)
        ]

        if new_callers:
            label_map = KNOWN_CALLERS.copy()
            msg = f"<b>New External Callers</b> ({len(new_callers)})\n\n"
            for c in new_callers:
                label = label_map.get(c["ip"], c["org"] or "Unknown")
                msg += f"  {c['ip']} ({label})\n"
                msg += f"  {c['calls']} requests | UA: {(c['ua'] or '')[:60]}\n\n"
            send_telegram(msg)
            log.info("Alerted %d new callers", len(new_callers))
        else:
            log.info("No new external callers (>10 req/24h)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
