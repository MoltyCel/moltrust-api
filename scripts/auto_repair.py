#!/usr/bin/env python3
"""auto_repair.py — autonomous 404-pattern detection + Telegram repair-candidate
digest. Read-only: it never changes code or data — it surfaces recurring 404
endpoints (>20 hits, aged >3 days, scanner noise excluded) so a human can decide
whether to add an alias/route. Intended cron: daily, after the reports.
"""
import os
import json
import re
import asyncio
import urllib.error
import urllib.request
from datetime import datetime, timezone

import asyncpg

import sys

# Der Pfad zum Repo, aus der Datei selbst. Python legt beim Skriptaufruf
# das Verzeichnis des Skripts auf sys.path, nicht das
# Arbeitsverzeichnis — ohne diese Zeile braucht der Aufruf ein
# PYTHONPATH aus der Crontab, und eine Crontab, die den Suchpfad setzt,
# ist dieselbe unsichtbare Ueberstimmung wie POLL_RPC_URL am 09.10.2026.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify

TELEGRAM_BOT_TOKEN = None
DB_URL = None

# Scanner/bot noise — never worth a repair candidate.
NOISE = r"(wp-includes|wp-admin|xmlrpc|/\.env|\.php|/\.git)"

# Paths whose 404 is the correct answer. Listed, not silently dropped: the
# footer says how many were held back, so a wrong entry here is visible.
#
# Each needs a reason, because "we decided not to build it" and "404 is what
# the specification asks for" are different claims and only the second one
# stays true without review.
EXPECTED_404 = [
    (r"^/\.well-known/oauth-(protected-resource|authorization-server)",
     "MCP authorization is OPTIONAL and this server requires none; protected-resource "
     "metadata MUST name at least one authorization server, so publishing it would be "
     "non-conformant. Both official SDKs only fetch it after a 401, which never comes."),
    (r"^/identity/resolve/",
     "An unregistered DID is a notFound resolution result, which is a 404 by design "
     "(see #741). The endpoint works; the identifier does not exist."),
]


def load_config():
    global TELEGRAM_BOT_TOKEN, DB_URL
    secrets_file = os.path.expanduser("~/.moltrust_secrets")
    if os.path.exists(secrets_file):
        with open(secrets_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    db_pw = os.environ.get("MOLTSTACK_DB_PW", "")
    DB_URL = f"postgresql://moltstack:{db_pw}@localhost:5432/moltstack"


async def find_repair_candidates(conn):
    """404 patterns >20 hits, first seen >3 days ago (persistent, not a blip).

    `last_status` is the status of the most recent request to that endpoint,
    whatever it was. A path fixed yesterday still carries hundreds of 404s
    inside the 30-day window, and for four days running the digest asked for
    /a2a/health and /.well-known/security.txt after both had been built. The
    404 count says what happened; the last status says what happens now.
    """
    return await conn.fetch(
        """
        WITH hits AS (
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
        )
        SELECT h.*,
               (SELECT r.status_code
                  FROM request_log r
                 WHERE r.endpoint = h.endpoint
                 ORDER BY r.ts DESC
                 LIMIT 1) AS last_status
          FROM hits h
         ORDER BY h.total_hits DESC
         LIMIT 25
        """,
        NOISE,
    )


def send_telegram(message, *, channel: str = notify.ALERTS):
    if not notify.telegram_allowed("auto_repair.send_telegram"):
        return
    if not TELEGRAM_BOT_TOKEN or not notify.chat_id_for(channel):
        print("Telegram not configured, skipping")
        return
    import urllib.request
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": notify.chat_id_for(channel),
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)  # nosec B310 - host is the literal Telegram API, only the bot token comes from env
    except Exception as e:
        print(f"Telegram error: {e}")


def expected_reason(endpoint: str) -> str | None:
    """The reason this 404 is correct, or None if it is not on the list."""
    for pattern, reason in EXPECTED_404:
        if re.search(pattern, endpoint):
            return reason
    return None


def probe(endpoint: str) -> int | None:
    """Current status for a GET of this path, or None if the probe failed.

    HEAD first, because a candidate is a path nobody has built and a GET to it
    may be the expensive one. A 405 means the method is wrong, not the path, so
    it falls back to GET. Only the public host is probed, and only read
    methods — nothing here may have a side effect.
    """
    url = f"https://api.moltrust.ch{endpoint}"
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", "moltrust-auto-repair/1.0 (internal 404 recheck)")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 - literal https host, path comes from our own request_log
                return resp.status
        except urllib.error.HTTPError as exc:
            if exc.code == 405 and method == "HEAD":
                continue
            return exc.code
        except Exception:
            return None
    return None


async def main():
    load_config()
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await find_repair_candidates(conn)
        now = datetime.now(timezone.utc)

        held, resolved, candidates = [], [], []
        for row in rows:
            endpoint = row["endpoint"]
            reason = expected_reason(endpoint)
            if reason:
                held.append((endpoint, row["total_hits"], reason))
                continue
            # Two independent signals that the path answers today, and both
            # are needed. The log is authoritative for what callers see — but
            # /a2a/health is in SKIP_LOG_PATHS, so its successful calls are
            # never written and its last logged status is still the 404 from
            # before it was built. 2538 hits, fixed on 2026-10-02, and the log
            # alone would keep asking for it forever. The probe answers 200.
            # Conversely the probe cannot speak for POST-only paths, where the
            # log can.
            status = probe(endpoint)
            if row["last_status"] is not None and row["last_status"] != 404:
                resolved.append((endpoint, f"last request answered {row['last_status']}"))
                continue
            if status is not None and status != 404:
                resolved.append((endpoint, f"probe answers {status}"))
                continue
            candidates.append((row, status))

        if not candidates:
            print(f"[{now.isoformat()}] No repair candidates "
                  f"({len(resolved)} resolved, {len(held)} expected)")

        lines = ["\U0001f527 <b>Auto-Repair Candidates</b> (recurring 404s)", ""]
        for row, status in candidates[:5]:
            days_old = (now - row["first_seen"]).days
            uas = str(row["user_agents"] or "")[:80]
            probed = "probe failed" if status is None else f"probe {status}"
            lines.append(f"<b>{row['endpoint']}</b>")
            lines.append(f"  {row['total_hits']} hits over {days_old}d · {probed} · UA: {uas}")
            lines.append("  → consider an alias/redirect or add the endpoint")
            lines.append("")

        if resolved:
            lines.append(f"<b>Answers now ({len(resolved)})</b> — dropped from the list:")
            for endpoint, why in resolved[:6]:
                lines.append(f"  {endpoint} · {why}")
            lines.append("")
        if held:
            lines.append(f"<b>Expected 404 ({len(held)})</b> — on the exception list:")
            for endpoint, hits, _ in held[:6]:
                lines.append(f"  {endpoint} · {hits} hits")
            lines.append("")

        if not candidates and not resolved and not held:
            return
        if candidates:
            lines.append("→ Manual review recommended. No auto-fix.")
        else:
            lines.append("→ Nothing to build. The list above is for the record.")

        send_telegram("\n".join(lines))
        print(f"[{now.isoformat()}] {len(candidates)} candidate(s), "
              f"{len(resolved)} resolved, {len(held)} expected")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
