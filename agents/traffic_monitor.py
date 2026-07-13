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
from datetime import datetime, timedelta, timezone

from app import notify

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
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
               (array_agg(DISTINCT ip_org) FILTER (WHERE ip_org IS NOT NULL))[1] AS ip_org
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
        })
    return callers


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


def format_telegram_message(new_callers, recurring_callers):
    """Format Telegram message (Markdown v1: *bold*, no **)"""
    total = len(new_callers) + len(recurring_callers)
    new_count = len(new_callers)

    if new_count == 0 and len(recurring_callers) <= 5:
        return None

    lines = [
        "🔍 *External Traffic Report*",
        "",
        f"*Total Active:* {total} callers",
        f"*Truly New:* {new_count}",
        f"*Recurring:* {len(recurring_callers)}",
        "",
    ]

    if new_callers:
        lines.append(f"🚨 *NEW External Callers ({new_count})*")
        lines.append("")
        for caller in new_callers:
            org = f" ({caller['ip_org']})" if caller['ip_org'] else ""
            ua_short = caller['user_agent'][:50]
            if len(caller['user_agent']) > 50:
                ua_short += "..."
            lines.append(f"`{caller['ip']}`{org}")
            lines.append(f"{caller['count']} reqs | UA: {ua_short}")
            lines.append("")

    if recurring_callers:
        lines.append("🔄 *Top Recurring Callers*")
        lines.append("")
        top_recurring = sorted(recurring_callers, key=lambda x: x['count'], reverse=True)[:5]
        for caller in top_recurring:
            org = f" ({caller['ip_org']})" if caller['ip_org'] else ""
            lines.append(f"`{caller['ip']}`{org} — {caller['count']} reqs")

    return "\n".join(lines)


def send_telegram_alert(message):
    """Send alert to Telegram"""
    if not notify.telegram_allowed("traffic_monitor.send_telegram_alert"):
        return False
    if not message or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={
                'chat_id': TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'Markdown',
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
    finally:
        conn.close()

    message = format_telegram_message(new_callers, recurring_callers)
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
