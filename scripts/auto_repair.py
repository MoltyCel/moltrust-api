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
import ipaddress
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
    (r"^/\.well-known/http-message-signatures-directory$",
     "MolTrust signs no outgoing requests; no signing key is planned (decision "
     "22.10.2026, ops AR-1022). Web Bot Auth reads this directory for a client that "
     "signs, so an empty one would claim a capability we lack. 149 hits from 11 IPs "
     "since 21.09.2026, crawlers asking every host (ops AR-404-4)."),
    (r"^/api/credentials$",
     "No credential collection exists to list: credentials are POSTed to "
     "/credentials/issue and /credentials/verify. 127 of 128 foreign hits came from "
     "one sweep, one /24 on 26.09.2026, 2311 requests over 413 paths (ops AR-1022)."),
    (r"^/api/did/resolve$",
     "Resolution is /identity/resolve/{did}. No request in 30 days carried a DID in "
     "the path, none in the 14 days of nginx log carried one in the query; 127 of 128 "
     "foreign hits are the same 26.09.2026 sweep, so an alias would serve nobody "
     "(ops AR-1022)."),
    (r"^/api/registry$",
     "No /api/ prefix exists. The agent registry is published as "
     "https://moltrust.ch/registry-proof.json, MolCheck's as /molcheck/registry. "
     "127 of 128 foreign hits are the 26.09.2026 sweep from one /24 (ops AR-1024)."),
    (r"^/\.well-known/ucp$",
     "Universal Commerce Protocol discovery. MolTrust runs no UCP merchant endpoint, "
     "so there is no profile to publish (ops AR-1024)."),
    (r"^/\.well-known/acp\.json$",
     "Agentic Commerce Protocol discovery, a merchant checkout for agents. MolTrust "
     "runs no ACP merchant endpoint, so there is no manifest to publish; same case as "
     "/.well-known/ucp. 88 of 92 foreign hits are one GuzzleHttp/7 client (ops AR-1027)."),
    (r"^/\.well-known/openid-configuration$",
     "OpenID Connect discovery. MolTrust is no OpenID provider and issues no ID "
     "tokens; an empty configuration would claim one (ops AR-1024)."),
    (r"(\{|%7[Bb])[A-Za-z_]\w*(:\w+)?(\}|%7[Dd])",
     "A literal OpenAPI path template: the caller copied the documented path without "
     "filling in the parameter. The route answers for a real identifier, and "
     "'{credential_id}' is none, so 404 is the correct answer. 25.10.2026: "
     "/identity/spiffe/{spiffe_uri}, /music/credential/{credential_id}, "
     "/music/verify/{credential_id}, /attestations/{attestation_id} (ops AR-1025)."),
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

    The status of the last request comes from `fetch_last_requests`, not
    from here: it has to leave our own requests out, and only
    `ist_eigene_anfrage` decides which those are.
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
            SELECT endpoint
              FROM hits h
             GROUP BY endpoint
            HAVING SUM(hits) > 20
               AND MIN(first_seen) < NOW() - INTERVAL '3 days'
        )
        SELECT h.endpoint, h.user_agent, h.hits, h.first_seen, h.last_seen
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
            "last_status": row.get("last_status"),
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


async def fetch_last_requests(conn, endpoints):
    """The last request per endpoint and User-Agent, any status, 30 days.

    Grouped by User-Agent so `letzter_status` can leave our own out with the
    one definition in `ist_eigene_anfrage`, instead of a second one in SQL.
    """
    if not endpoints:
        return []
    return await conn.fetch(
        """
        SELECT DISTINCT ON (endpoint, COALESCE(user_agent, 'unknown'))
               endpoint, COALESCE(user_agent, 'unknown') AS user_agent,
               status_code, ts
          FROM request_log
         WHERE endpoint = ANY($1::text[])
           AND ts > NOW() - INTERVAL '30 days'
         ORDER BY endpoint, COALESCE(user_agent, 'unknown'), ts DESC
        """,
        list(endpoints),
    )


def letzter_status(rows) -> dict[str, int]:
    """{endpoint: status of the last request from a stranger}.

    The last status says what happens now (a path fixed yesterday still has
    hundreds of 404s in the window: /a2a/health and /.well-known/security.txt
    sat in the digest four days after both were built). But only a stranger's
    request says it. Our own recheck asks every path once a day with GET, and
    for a literal template like /music/verify/{credential_id} that is a 404,
    while the strangers' POST to the same path is a 405. Whose request came
    last decided the class, and the path went back and forth between "answers
    now" and candidate (ops AR-1025, Lars 25.10.2026). An endpoint with only
    our own requests has no entry.
    """
    out: dict[str, tuple] = {}
    for row in rows:
        if ist_eigene_anfrage(row["user_agent"]):
            continue
        e = row["endpoint"]
        if e not in out or row["ts"] > out[e][0]:
            out[e] = (row["ts"], row["status_code"])
    return {e: status for e, (_, status) in out.items()}


def nur_eigene_pfade(rows, now):
    """Paths the old rule would have listed and nobody outside ever asked for.

    Named, not silently dropped — the same reason EXPECTED_404 has a footer.
    A path that only our own programs ask for is no repair candidate, but it
    is worth seeing: 480 hourly 404s on /agent/score say something about our
    own check, not about a missing route.

    The condition is zero foreign hits, not "few". A path with 15 strangers
    and 10 of our own is a path below the threshold, like any other — the
    line here says "our own checks, not a missing route", and for that path
    the sentence would be false.
    """
    gesamt: dict[str, list] = {}
    for row in rows:
        p = gesamt.setdefault(row["endpoint"], [0, 0, None])
        p[0] += row["hits"]
        if not ist_eigene_anfrage(row["user_agent"]):
            p[1] += row["hits"]
        if row.get("last_seen") is not None and (p[2] is None or row["last_seen"] > p[2]):
            p[2] = row["last_seen"]
    frisch = now - EIGENE_FRISCH
    return sorted(((e, t) for e, (t, f, zuletzt) in gesamt.items()
                   if f == 0 and zuletzt is not None and zuletzt >= frisch),
                  key=lambda x: x[1], reverse=True)


# A path stays in "Only us" while our own programs still get a 404 there. Our
# checks run hourly or daily, so a path still asked has a 404 younger than a
# day; three days leave room for a missed run. Without this a fixed path sat in
# the line until its last 404 left the 30-day window: the hosted MCP server
# asked /guard/skill/audit on the API port, last 404 on 24.10.2026, and the
# line would have named it until 23.11. (ops AR-1027).
EIGENE_FRISCH = timedelta(days=3)


# A path whose foreign hits came almost all from one network on one day was
# asked for by one sweep, not by callers who keep missing it. 26.09.2026: one
# /24 sent 2311 requests over 413 paths in eight hours, and three of those
# paths sat in the digest for a month (ops AR-1022, AR-1024).
EINMAL_ANTEIL = 0.9


async def fetch_hits_per_netz(conn, endpoints):
    """404s of the last 30 days per endpoint, UTC day, IP and User-Agent.

    Same window and filters as `fetch_hits_per_ua`, restricted to the paths
    that already passed the candidate rule. The User-Agent comes along so
    `ist_eigene_anfrage` decides here too, not a second definition in SQL.
    """
    if not endpoints:
        return []
    return await conn.fetch(
        """
        SELECT endpoint,
               (ts AT TIME ZONE 'UTC')::date AS tag,
               ip,
               COALESCE(user_agent, 'unknown') AS user_agent,
               COUNT(*) AS hits
          FROM request_log
         WHERE status_code = 404
           AND ts > NOW() - INTERVAL '30 days'
           AND endpoint = ANY($1::text[])
         GROUP BY 1, 2, 3, 4
        """,
        list(endpoints),
    )


def netz(ip: str | None) -> str:
    """The /24 of an IPv4 address, the /48 of an IPv6 one, else the input."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return ip or "unknown"
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def einmal_scans(rows) -> dict[str, dict]:
    """Paths whose foreign hits are at least 90 % from one network on one day.

    Returns {endpoint: {"netz", "tag", "hits", "fremd_hits"}}. Own requests do
    not count on either side of the share, the same as in `zu_pfaden`.
    """
    zellen: dict[str, dict[tuple, int]] = {}
    for row in rows:
        if ist_eigene_anfrage(row["user_agent"]):
            continue
        z = zellen.setdefault(row["endpoint"], {})
        key = (netz(row["ip"]), row["tag"])
        z[key] = z.get(key, 0) + row["hits"]
    out = {}
    for endpoint, z in zellen.items():
        fremd = sum(z.values())
        (net, tag), hits = max(z.items(), key=lambda kv: kv[1])
        if fremd and hits >= EINMAL_ANTEIL * fremd:
            out[endpoint] = {"netz": net, "tag": tag, "hits": hits, "fremd_hits": fremd}
    return out


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
        letzte = letzter_status(await fetch_last_requests(
            conn, [r["endpoint"] for r in rows]))
        for row in rows:
            row["last_status"] = letzte.get(row["endpoint"])
        eigene = nur_eigene_pfade(alle, now)
        scans = einmal_scans(await fetch_hits_per_netz(
            conn, [r["endpoint"] for r in rows]))

        held, resolved, einmal, candidates = [], [], [], []
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
            if endpoint in scans:
                einmal.append((endpoint, scans[endpoint]))
                continue
            candidates.append((row, status))

        if not candidates:
            print(f"[{now.isoformat()}] No repair candidates "
                  f"({len(resolved)} resolved, {len(held)} expected, "
                  f"{len(einmal)} one-off, {len(eigene)} only us)")

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
        if einmal:
            lines.append(f"<b>Einmal-Scans ({len(einmal)})</b> — one /24, one day, "
                         f"\u2265{round(100 * EINMAL_ANTEIL)}% of foreign hits:")
            for endpoint, s in einmal[:6]:
                lines.append(f"  {endpoint} · {s['netz']} on {s['tag']} · "
                             f"{s['hits']}/{s['fremd_hits']} foreign")
            lines.append("")
        if eigene:
            lines.append(f"<b>Only us ({len(eigene)})</b> — our own checks, "
                         f"not a missing route:")
            for endpoint, hits in eigene[:6]:
                lines.append(f"  {endpoint} · {hits} hits from moltrust-* "
                             f"· 0/{hits} foreign (0%)")
            lines.append("")

        if not candidates and not resolved and not held and not einmal and not eigene:
            return
        if candidates:
            lines.append("→ Manual review recommended. No auto-fix.")
        else:
            lines.append("→ Nothing to build. The list above is for the record.")

        send_telegram("\n".join(lines))
        print(f"[{now.isoformat()}] {len(candidates)} candidate(s), "
              f"{len(resolved)} resolved, {len(held)} expected, "
              f"{len(einmal)} one-off, {len(eigene)} only us")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
