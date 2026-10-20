#!/usr/bin/env python3
"""
Traffic Monitor v3 — Authoritative known_callers ledger

"Truly new" = an IP whose first_seen in the known_callers ledger is within the
last NEW_WINDOW_HOURS. The ledger is the single source of truth: it is backfilled
once from request_log MIN(ts) and self-heals — every run upserts active IPs
(full IP, never /24-masked) with their true first-seen.

Replaces v2's flat-file (known_ips.txt) heuristic, which mislabeled every active
IP as "new" on first run / after any state-file reset (the "25-30 new callers"
noise). known_callers is now full-IP keyed; curated rows (label/category) are
left untouched via ON CONFLICT DO NOTHING.
"""

import psycopg2
import psycopg2.extras
import requests
import os
import json
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone

import sys

# Der Pfad zum Repo, aus der Datei selbst. Python legt beim Skriptaufruf
# das Verzeichnis des Skripts auf sys.path, nicht das
# Arbeitsverzeichnis — ohne diese Zeile braucht der Aufruf ein
# PYTHONPATH aus der Crontab, und eine Crontab, die den Suchpfad setzt,
# ist dieselbe unsichtbare Ueberstimmung wie POLL_RPC_URL am 09.10.2026.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DB_PASSWORD = os.getenv('MOLTSTACK_DB_PW', '')
TRUSTED_PREFIXES = ['127.', '::1', '10.', '172.16.', '192.168.', '88.99.', '116.202.', '46.225.175.']

NEW_WINDOW_HOURS = 24      # first_seen newer than this => "truly new"
ACTIVE_WINDOW_HOURS = 25   # lookback for "active" callers
MIN_REQUESTS = 10          # min requests in active window to count


def db_connect():
    return psycopg2.connect(
        host="localhost",
        database="moltstack",
        user="moltstack",
        password=DB_PASSWORD,
    )


def is_trusted_ip(ip):
    """Check if IP is from trusted sources (localhost, private ranges, Hetzner)"""
    return any(ip.startswith(prefix) for prefix in TRUSTED_PREFIXES)


def get_external_callers(conn):
    """Active external callers (> MIN_REQUESTS in ACTIVE_WINDOW_HOURS), each
    annotated with its authoritative first_seen: the known_callers ledger value,
    falling back to request_log MIN(ts) for IPs not yet in the ledger (so a
    brand-new IP is classified correctly even before its upsert lands)."""
    query = """
    WITH active AS (
        SELECT ip,
               COUNT(*)                                                          AS request_count,
               MAX(ts)                                                           AS last_seen,
               (array_agg(DISTINCT user_agent))[1]                               AS user_agent,
               (array_agg(DISTINCT ip_org) FILTER (WHERE ip_org IS NOT NULL))[1] AS ip_org,
               COUNT(*) FILTER (WHERE agent_did IS NOT NULL)                     AS auth_requests,
               COUNT(DISTINCT agent_did)                                         AS auth_dids
        FROM request_log
        WHERE ts > NOW() - make_interval(hours => %s) AND ip IS NOT NULL
        GROUP BY ip
        HAVING COUNT(*) > %s
    ),
    firstseen AS (
        SELECT a.ip, MIN(rl.ts) AS first_ever
        FROM active a JOIN request_log rl USING (ip)
        GROUP BY a.ip
    )
    SELECT a.ip,
           a.request_count,
           a.last_seen,
           a.user_agent,
           a.ip_org,
           a.auth_requests,
           a.auth_dids,
           COALESCE(kc.first_seen, f.first_ever) AS first_seen,
           (kc.ip IS NOT NULL)                   AS in_ledger
    FROM active a
    JOIN firstseen f USING (ip)
    LEFT JOIN known_callers kc ON kc.ip = a.ip
    ORDER BY a.request_count DESC
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (ACTIVE_WINDOW_HOURS, MIN_REQUESTS))
        rows = cur.fetchall()

    callers = []
    for r in rows:
        if is_trusted_ip(r['ip']):
            continue
        callers.append({
            'ip': r['ip'],
            'count': r['request_count'],
            'last_seen': r['last_seen'],
            'first_seen': r['first_seen'],
            'in_ledger': r['in_ledger'],
            'user_agent': r['user_agent'] or 'Unknown',
            'ip_org': r['ip_org'] or '',
            'auth_requests': r['auth_requests'],
            'auth_dids': r['auth_dids'],
        })
    return callers


# Self-identifying crawlers, probers and verifiers. Nearly all of them carry a
# +URL in the user agent, which is what makes the class readable at all: the
# operator wanted to be recognised. Matched case-insensitively as substrings,
# because the version suffix changes and the name does not.
PROBER_MARKERS = (
    # x402 and A2A ecosystem probes
    "x402-census-probe", "x402-reliability-probe", "x402-observer", "x402-client",
    "402explorer", "allow402-quote", "enclave402", "agent402", "the402",
    "nohumans.directory-probe", "agent-tools.cloud-a2a", "agenstrybot",
    "knowngood-verifier", "brickbluebot", "ziwei-alliance-marketing",
    "8004scan", "erc-8004-prober", "waggle",
    # health and uptime
    "carbonmonitor", "mako-pulse-prober", "endurance-cycle", "healthcheck",
    "hermes-readonly-audit",
    # LLM crawlers, per the robots.txt whitelist
    "gptbot", "chatgpt-user", "oai-searchbot", "claudebot", "anthropic-ai",
    "claude-web", "google-extended", "applebot-extended", "perplexitybot",
    "cohere-ai", "ccbot",
)

# Plain HTTP libraries. A caller here said nothing about itself, so the class is
# "unknown", not "human" and not "prober".
CLIENT_MARKERS = ("curl/", "python-httpx", "python-requests", "go-http-client",
                  "axios/", "guzzlehttp", "okhttp", "node", "libwww", "wget/")


def classify_ua(user_agent):
    """prober | client | browser | none — what the caller says it is.

    Deliberately not a judgement about intent. A self-declared prober is the
    easy case; everything else splits into "a library" and "a string shaped
    like a browser", and neither tells us whether a person is behind it.
    """
    ua = (user_agent or "").strip().lower()
    if not ua or ua == "unknown":
        return "none"
    if any(m in ua for m in PROBER_MARKERS):
        return "prober"
    if any(m in ua for m in CLIENT_MARKERS):
        return "client"
    if ua.startswith("mozilla/"):
        return "browser"
    return "client"


def active_dids(conn, days=7):
    """Distinct DIDs behind an authenticated call, by how they authenticated.

    Two sources, because two things authenticate. An API key resolves to a DID
    in request_log; a signed gate header is decided inside MoltGuard and lands
    in gate_decisions. Counting only the first would report the gate as unused
    even while it runs, and the two sets overlap, so the union is taken rather
    than the sum.

    This is a count of identities, not of IPs: one agent behind a shared egress
    counts once, and one IP carrying forty agents counts forty.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH per_key AS (
                SELECT DISTINCT agent_did AS did FROM request_log
                 WHERE agent_did IS NOT NULL AND ts > NOW() - make_interval(days => %s)
            ), per_gate AS (
                SELECT DISTINCT did FROM gate_decisions
                 WHERE ts > NOW() - make_interval(days => %s)
            )
            SELECT (SELECT count(*) FROM per_key),
                   (SELECT count(*) FROM per_gate),
                   (SELECT count(*) FROM (SELECT did FROM per_key
                                          UNION SELECT did FROM per_gate) u)
            """,
            (days, days),
        )
        by_key, by_gate, combined = cur.fetchone()
    return {"key": by_key, "gate": by_gate, "total": combined}


def upsert_known_callers(conn, callers):
    """Persist active IPs into the known_callers ledger (full IP + true first_seen).
    ON CONFLICT DO NOTHING keeps any existing/curated first_seen, label and
    category untouched — we never overwrite hand-curated rows."""
    if not callers:
        return 0
    rows = []
    for c in callers:
        label = f"{c['ip_org']} — {c['user_agent']}".strip(' —') or None
        rows.append((c['ip'], c['first_seen'], (label or '')[:128] or None, 'auto'))
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO known_callers (ip, first_seen, label, category) VALUES %s "
            "ON CONFLICT (ip) DO NOTHING",
            rows,
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


def categorize_callers(callers):
    """truly new = first_seen within NEW_WINDOW_HOURS; recurring = older."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=NEW_WINDOW_HOURS)
    new_callers = [c for c in callers if c['first_seen'] > cutoff]
    recurring_callers = [c for c in callers if c['first_seen'] <= cutoff]
    return new_callers, recurring_callers


def format_telegram_message(new_callers, recurring_callers, dids=None):
    """Format Telegram message (Markdown v1: *bold*, no **)"""
    total = len(new_callers) + len(recurring_callers)
    new_count = len(new_callers)

    if new_count == 0 and len(recurring_callers) <= 5:
        return None

    # HTML rather than Markdown, and every interpolated value escaped. User
    # agents and IP org names routinely carry `_`, `*` and backticks; under
    # Markdown a single one of those breaks the whole message and Telegram
    # answers 400, which the sender reports as a plain failure to send.
    esc = notify.escape_html
    lines = [
        "🔍 <b>External Traffic Report</b>",
        "",
        f"<b>Total Active:</b> {total} callers",
        f"<b>Truly New:</b> {new_count}",
        f"<b>Recurring:</b> {len(recurring_callers)}",
    ]

    # Identities, not addresses. The caller counts above are IPs, and an IP is
    # neither an agent nor a person: 104.30.180.0 is Cloudflare WARP and carries
    # dozens of unrelated agents behind one address.
    if dids:
        gate_note = f", {dids['gate']} via gate header" if dids["gate"] else ""
        lines.append(f"<b>Active DIDs (7d):</b> {dids['total']} "
                     f"({dids['key']} via API key{gate_note})")

    if recurring_callers:
        by_class = Counter(classify_ua(c["user_agent"]) for c in recurring_callers)
        with_did = sum(1 for c in recurring_callers if c["auth_dids"])
        named = ", ".join(f"{by_class[k]} {k}" for k in
                          ("prober", "client", "browser", "none") if by_class[k])
        lines.append(f"<b>Of those recurring:</b> {named}")
        lines.append(f"<b>Carrying a DID:</b> {with_did} of {len(recurring_callers)}")

    lines.append("")

    if new_callers:
        lines.append(f"🚨 <b>NEW External Callers ({new_count})</b>")
        lines.append("")
        for caller in new_callers:
            org = f" ({esc(caller['ip_org'])})" if caller['ip_org'] else ""
            ua_short = caller['user_agent'][:50]
            if len(caller['user_agent']) > 50:
                ua_short += "..."
            lines.append(f"<code>{esc(caller['ip'])}</code>{org}")
            lines.append(f"{caller['count']} reqs | UA: {esc(ua_short)}")
            lines.append("")

    if recurring_callers:
        lines.append("🔄 <b>Top Recurring Callers</b>")
        lines.append("")
        top_recurring = sorted(recurring_callers, key=lambda x: x['count'], reverse=True)[:5]
        for caller in top_recurring:
            org = f" ({esc(caller['ip_org'])})" if caller['ip_org'] else ""
            did_note = (f", {caller['auth_dids']} DID"
                        f"{'s' if caller['auth_dids'] != 1 else ''}"
                        if caller["auth_dids"] else "")
            lines.append(f"<code>{esc(caller['ip'])}</code>{org} — "
                         f"{caller['count']} reqs{did_note}")

    return "\n".join(lines)


def send_telegram_alert(message, *, channel: str = notify.STATS):
    """Send alert to Telegram"""
    if not notify.telegram_allowed("traffic_monitor.send_telegram_alert"):
        return False
    if not message or not TELEGRAM_BOT_TOKEN or not notify.chat_id_for(channel):
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                'chat_id': notify.chat_id_for(channel),
                'text': message,
                'parse_mode': 'HTML',
            },
            timeout=10,
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram send error: {e}")
        return False


STATE_FILE = os.path.expanduser("~/.moltstack/traffic_state.json")


def traffic_signature(new_callers, recurring_callers):
    """Stable hash of (new_count, sorted recurring IPs). Identical steady-state -> identical signature."""
    payload = {
        "new_count": len(new_callers),
        "recurring": sorted(c["ip"] for c in recurring_callers),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def load_last_signature():
    """Last sent signature, or None if no state / unreadable."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("signature")
    except (OSError, ValueError):
        return None


def save_last_signature(sig):
    """Persist signature atomically (creates ~/.moltstack/ if missing)."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"signature": sig, "updated_at": datetime.now(timezone.utc).isoformat()}, f)
    os.replace(tmp, STATE_FILE)


def main():
    """Main traffic monitor — known_callers ledger as source of truth"""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Traffic Monitor v3 starting")

    conn = db_connect()
    try:
        current_callers = get_external_callers(conn)
        print(f"  Active external callers (>{MIN_REQUESTS} reqs/{ACTIVE_WINDOW_HOURS}h): {len(current_callers)}")

        new_callers, recurring_callers = categorize_callers(current_callers)
        print(f"  Truly new (<{NEW_WINDOW_HOURS}h): {len(new_callers)}, Recurring: {len(recurring_callers)}")

        inserted = upsert_known_callers(conn, current_callers)
        print(f"  Ledger upsert: {inserted} new IP(s) added to known_callers")

        dids = active_dids(conn)
        print(f"  Active DIDs (7d): {dids['total']} "
              f"({dids['key']} via API key, {dids['gate']} via gate header)")
    finally:
        conn.close()

    message = format_telegram_message(new_callers, recurring_callers, dids)
    if not message:
        print(f"  No alert — quiet period")
    else:
        sig = traffic_signature(new_callers, recurring_callers)
        if sig == load_last_signature():
            print("  Unchanged since last run — alert suppressed")
        else:
            success = send_telegram_alert(message)
            print(f"  Telegram alert sent: {success}")
            if success:
                save_last_signature(sig)


if __name__ == "__main__":
    main()
