#!/usr/bin/env python3
"""auto_repair.py — autonomous 404-pattern detection + Telegram repair-candidate
digest. Read-only: it never changes code or data — it surfaces recurring 404
endpoints (>20 hits from strangers, aged >3 days, scanner noise excluded) so a
human can decide whether to add an alias/route. Requests from our own programs
are named `moltrust-*` and are counted separately, because a path we ask for
ourselves every hour is not a path anyone is missing (ops AR-404-2). Intended
cron: daily, after the reports.
"""
import os
import json
import re
import asyncio
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

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


async def fetch_hits_per_ua(conn):
    """404s of the last 30 days per endpoint and User-Agent.

    Grouped one level finer than the digest needs, because the question "who
    asked" can only be answered per User-Agent. The two conditions here are a
    superset of the ones that decide a candidate: an endpoint under 21 hits in
    total cannot have more than 20 from strangers, and an endpoint whose very
    first 404 is younger than three days has no older one from a stranger
    either. The exact rule runs in `zu_pfaden`, over the rows this returns.

    `last_status` is the status of the most recent request to that endpoint,
    whatever it was and whoever sent it. A path fixed yesterday still carries
    hundreds of 404s inside the 30-day window, and for four days running the
    digest asked for /a2a/health and /.well-known/security.txt after both had
    been built. The 404 count says what happened; the last status says what
    happens now.
    """
    return await conn.fetch(
        """
        WITH hits AS (
            SELECT endpoint,
                   COALESCE(user_agent, 'unknown') AS user_agent,
                   COUNT(*) AS hits,
                   MIN(ts)  AS first_seen,
                   MAX(ts)  AS last_seen
              FROM request_log
             WHERE status_code = 404
               AND ts > NOW() - INTERVAL '30 days'
               AND endpoint <> '/'
               AND endpoint !~* $1
             GROUP BY endpoint, COALESCE(user_agent, 'unknown')
        ),
        pfade AS (
            SELECT endpoint,
                   (SELECT r.status_code
                      FROM request_log r
                     WHERE r.endpoint = h.endpoint
                     ORDER BY r.ts DESC
                     LIMIT 1) AS last_status
              FROM hits h
             GROUP BY endpoint
            HAVING SUM(hits) > 20
               AND MIN(first_seen) < NOW() - INTERVAL '3 days'
        )
        SELECT h.endpoint, h.user_agent, h.hits, h.first_seen, h.last_seen,
               p.last_status
          FROM hits h
          JOIN pfade p USING (endpoint)
         ORDER BY h.endpoint, h.hits DESC
        """,
        NOISE,
    )


def ist_eigene_anfrage(user_agent: str | None) -> bool:
    """True for a request one of our own programs sent.

    Every one of them names itself with the same prefix: the digest's own
    recheck is `moltrust-auto-repair/1.0`, the hourly artefact check is
    `moltrust-artefact-check/1.0`. Both appeared in the report of 2026-10-16
    inside the hit counts, so the digest was partly asking for paths because
    it had asked for them itself (ops AR-404-2).

    A missing User-Agent counts as a stranger. We know what our own programs
    send; an unnamed caller is someone we cannot account for, and dropping it
    would hide a real complaint.
    """
    if not user_agent:
        return False
    return user_agent.strip().lower().startswith("moltrust-")


def zu_pfaden(rows, now):
    """One record per endpoint, counted over strangers only.

    Returns the candidates in the digest's order: most foreign hits first, at
    most 25. `total_hits` stays in the record, because the share of foreign
    requests is the second thing the report has to say — an endpoint with 200
    hits of which 2 are foreign is a different fact from one with 200 of which
    198 are.
    """
    pfade: dict[str, dict] = {}
    for row in rows:
        p = pfade.setdefault(row["endpoint"], {
            "endpoint": row["endpoint"],
            "total_hits": 0,
            "fremd_hits": 0,
            "first_seen": None,
            "last_seen": None,
            "last_status": row["last_status"],
            "user_agents": [],
        })
        p["total_hits"] += row["hits"]
        if row["last_seen"] is not None and (
                p["last_seen"] is None or row["last_seen"] > p["last_seen"]):
            p["last_seen"] = row["last_seen"]
        if not ist_eigene_anfrage(row["user_agent"]):
            p["fremd_hits"] += row["hits"]
            p["user_agents"].append(row["user_agent"])
            # Only a stranger's 404 dates the problem. Our own recheck runs
            # daily, so its first hit is always three days old by now and
            # would carry every endpoint past the age condition.
            if row["first_seen"] is not None and (
                    p["first_seen"] is None or row["first_seen"] < p["first_seen"]):
                p["first_seen"] = row["first_seen"]

    drei_tage = now - timedelta(days=3)
    kandidaten = [p for p in pfade.values()
                  if p["fremd_hits"] > 20
                  and p["first_seen"] is not None
                  and p["first_seen"] < drei_tage]
    kandidaten.sort(key=lambda p: p["fremd_hits"], reverse=True)
    return kandidaten[:25]


def nur_eigene_pfade(rows):
    """Paths the old rule would have listed and the UA filter takes out.

    Named, not silently dropped — the same reason EXPECTED_404 has a footer.
    A path that only our own programs ask for is no repair candidate, but it
    is worth seeing: 480 hourly 404s on /agent/score say something about our
    own check, not about a missing route.
    """
    gesamt: dict[str, list[int]] = {}
    for row in rows:
        p = gesamt.setdefault(row["endpoint"], [0, 0])
        p[0] += row["hits"]
        if not ist_eigene_anfrage(row["user_agent"]):
            p[1] += row["hits"]
    return sorted(((e, t - f) for e, (t, f) in gesamt.items() if f <= 20),
                  key=lambda x: x[1], reverse=True)


def anteil_fremd(pfad) -> str:
    """"142/180 foreign (79%)" — the share the report prints per path.

    The hit count beside it is the foreign one. Both numbers belong in the
    line: the count says how much of a problem this is, the share says how
    much of what the log shows was us asking ourselves.
    """
    fremd, gesamt = pfad["fremd_hits"], pfad["total_hits"]
    if not gesamt:
        return "no hits"
    return f"{fremd}/{gesamt} foreign ({round(100 * fremd / gesamt)}%)"


def ua_zeile(pfad, grenze: int = 80) -> str:
    """The foreign User-Agents of this path, shortened for Telegram."""
    uas = "; ".join(pfad["user_agents"])
    return uas[:grenze] if uas else "none"


async def find_repair_candidates(conn):
    """404 patterns >20 foreign hits, first seen >3 days ago."""
    rows = await fetch_hits_per_ua(conn)
    return zu_pfaden(rows, datetime.now(timezone.utc))


def send_telegram(message, *, channel: str = notify.ALERTS):
    # Through app/notify.py: gate, throttle and send log apply here too. Until
    # 2026-10-12 this built the Telegram URL itself and posted with urllib
    # (issue #744), which the single-sender check did not see.
    notify.send_telegram(message, channel=channel, parse_mode="HTML")


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
        alle = await fetch_hits_per_ua(conn)
        now = datetime.now(timezone.utc)
        rows = zu_pfaden(alle, now)
        eigene = nur_eigene_pfade(alle)

        held, resolved, candidates = [], [], []
        for row in rows:
            endpoint = row["endpoint"]
            reason = expected_reason(endpoint)
            if reason:
                held.append((endpoint, row["fremd_hits"], anteil_fremd(row), reason))
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
                  f"({len(resolved)} resolved, {len(held)} expected, "
                  f"{len(eigene)} only us)")

        lines = ["\U0001f527 <b>Auto-Repair Candidates</b> (recurring 404s)", ""]
        for row, status in candidates[:5]:
            days_old = (now - row["first_seen"]).days
            probed = "probe failed" if status is None else f"probe {status}"
            lines.append(f"<b>{row['endpoint']}</b>")
            lines.append(f"  {row['fremd_hits']} hits over {days_old}d · {probed}"
                         f" · {anteil_fremd(row)}")
            lines.append(f"  UA: {ua_zeile(row)}")
            lines.append("  → consider an alias/redirect or add the endpoint")
            lines.append("")

        if resolved:
            lines.append(f"<b>Answers now ({len(resolved)})</b> — dropped from the list:")
            for endpoint, why in resolved[:6]:
                lines.append(f"  {endpoint} · {why}")
            lines.append("")
        if held:
            lines.append(f"<b>Expected 404 ({len(held)})</b> — on the exception list:")
            for endpoint, hits, anteil, _ in held[:6]:
                lines.append(f"  {endpoint} · {hits} hits · {anteil}")
            lines.append("")
        if eigene:
            lines.append(f"<b>Only us ({len(eigene)})</b> — our own checks, "
                         f"not a missing route:")
            for endpoint, hits in eigene[:6]:
                lines.append(f"  {endpoint} · {hits} hits from moltrust-*")
            lines.append("")

        if not candidates and not resolved and not held and not eigene:
            return
        if candidates:
            lines.append("→ Manual review recommended. No auto-fix.")
        else:
            lines.append("→ Nothing to build. The list above is for the record.")

        send_telegram("\n".join(lines))
        print(f"[{now.isoformat()}] {len(candidates)} candidate(s), "
              f"{len(resolved)} resolved, {len(held)} expected, "
              f"{len(eigene)} only us")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
