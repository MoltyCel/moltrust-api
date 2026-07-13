#!/usr/bin/env python3
"""auto_repair.py — autonomous 404-pattern detection + Telegram repair-candidate
digest. Read-only: it never changes code or data — it surfaces recurring 404
endpoints (>20 hits, aged >3 days, scanner noise excluded) so a human can decide
whether to add an alias/route. Intended cron: daily, after the reports.
"""
import os
import json
import asyncio
from datetime import datetime, timezone

import asyncpg

from app import notify

TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None
DB_URL = None

# Scanner/bot noise — never worth a repair candidate.
NOISE = r"(wp-includes|wp-admin|xmlrpc|/\.env|\.php|/\.git)"


def load_config():
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DB_URL
    secrets_file = os.path.expanduser("~/.moltrust_secrets")
    if os.path.exists(secrets_file):
        with open(secrets_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
    db_pw = os.environ.get("MOLTSTACK_DB_PW", "")
    DB_URL = f"postgresql://moltstack:{db_pw}@localhost:5432/moltstack"


async def find_repair_candidates(conn):
    """404 patterns >20 hits, first seen >3 days ago (persistent, not a blip)."""
    return await conn.fetch(
        """
        SELECT endpoint,
               COUNT(*) AS total_hits,
               MIN(ts)  AS first_seen,
               MAX(ts)  AS last_seen,
               STRING_AGG(DISTINCT COALESCE(user_agent, 'unknown'), '; ') AS user_agents
        FROM request_log
        WHERE status_code = 404
          AND ts > NOW() - INTERVAL '30 days'
          AND endpoint <> '/'
          AND endpoint !~* $1
        GROUP BY endpoint
        HAVING COUNT(*) > 20
           AND MIN(ts) < NOW() - INTERVAL '3 days'
        ORDER BY total_hits DESC
        LIMIT 5
        """,
        NOISE,
    )


def send_telegram(message):
    if not notify.telegram_allowed("auto_repair.send_telegram"):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured, skipping")
        return
    import urllib.request
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


async def main():
    load_config()
    conn = await asyncpg.connect(DB_URL)
    try:
        candidates = await find_repair_candidates(conn)
        if not candidates:
            print(f"[{datetime.now(timezone.utc).isoformat()}] No repair candidates")
            return
        lines = ["\U0001f527 <b>Auto-Repair Candidates</b> (recurring 404s)", ""]
        now = datetime.now(timezone.utc)
        for row in candidates:
            days_old = (now - row["first_seen"]).days
            uas = str(row["user_agents"] or "")[:80]
            lines.append(f"<b>{row['endpoint']}</b>")
            lines.append(f"  {row['total_hits']} hits over {days_old}d · UA: {uas}")
            lines.append("  → consider an alias/redirect or add the endpoint")
            lines.append("")
        lines.append("→ Manual review recommended. No auto-fix.")
        send_telegram("\n".join(lines))
        print(f"[{now.isoformat()}] Sent {len(candidates)} candidate(s) to Telegram")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
