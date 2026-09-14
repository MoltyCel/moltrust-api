"""Usage instrumentation: key fingerprints, traffic classes, daily rollups.

``request_log`` keeps 30 days and is owned by the ``postgres`` role. Everything
here exists so that a question about the last N months has an answer once those
rows are gone, and so that the answer separates real callers from the scanners
and monitors that make up most of the traffic.
"""
from __future__ import annotations

import hashlib
import re

# Rollups outlive their source by two years.
ROLLUP_RETENTION_MONTHS = 24


def key_fingerprint(api_key: str) -> str | None:
    """Irreversible, stable handle for an API key.

    Counting distinct keys per day needs identity, not the secret. A truncated
    SHA-256 gives identity; the key itself never reaches a table that reporting
    queries touch.
    """
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


# Path segments that are values rather than routes. Left unbounded, a metering
# key like "GET /identity/verify/did:moltrust:6d5c9d50d2c34ad0" would mint one
# row per subject and the meter would grow without limit.
_SEGMENT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^did:[a-z0-9]+:.+$", re.I), "{did}"),
    (re.compile(r"^0x[0-9a-f]{40}$", re.I), "{address}"),
    (re.compile(r"^0x[0-9a-f]{64}$", re.I), "{hash}"),
    (re.compile(r"^[0-9]+$"), "{id}"),
    (re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I), "{uuid}"),
    (re.compile(r"^[0-9a-f]{16,}$", re.I), "{hex}"),
]

_MAX_KEY_LEN = 120


def bounded_endpoint_key(method: str, path: str) -> str:
    """Collapse a concrete path to a bounded metering key.

    ``app.credits.resolve_endpoint_key`` maps the priced routes and falls
    through to the raw path for everything else. That fallthrough is what would
    blow the cardinality up, so it is templated here instead.
    """
    try:
        from app.credits import resolve_endpoint_key

        key = resolve_endpoint_key(method, path)
        # A mapped route already carries its template.
        if "{" in key:
            return key[:_MAX_KEY_LEN]
    except Exception:
        pass

    segments = path.split("/")
    out: list[str] = []
    for seg in segments:
        replaced = seg
        for pattern, placeholder in _SEGMENT_PATTERNS:
            if pattern.match(seg):
                replaced = placeholder
                break
        out.append(replaced)
    return f"{method} {'/'.join(out)}"[:_MAX_KEY_LEN]


# Traffic classification, evaluated in SQL so the rollup stays a single
# statement over rows that are about to be deleted.
#
# Order is the whole point: a scanner that sends a Chrome user-agent is a
# scanner, and the 90-day sweep found exactly that — 37,524 of 48,305
# "browser" requests came from two vulnerability scanners spoofing Chrome. The
# first arm that matches wins.
TRAFFIC_CLASS_SQL = """
CASE
  WHEN r.ip = '127.0.0.1' OR r.ip LIKE '46.225.175.%' OR r.ip LIKE '10.%'
       OR r.ip LIKE '192.168.%' OR r.ip LIKE '172.16.%'
    THEN 'self'
  WHEN r.user_agent ~* '(uptime-kuma|upptime|uptimerobot|pingdom|statuscake|payforapi-health|a2a-registry-healthcheck|gold-402-verifier|terminus-observatory|blackbox_exporter|newrelic|datadog)'
    THEN 'monitor'
  WHEN ipday.requests_that_day > __BULK_THRESHOLD__
    THEN 'bulk'
  WHEN r.user_agent ~* '(sqlmap|nikto|nuclei|feroxbuster|masscan|zgrab|censys|shodan|internetmeasurement|expanse|dirbuster|gobuster|wpscan)'
       OR r.endpoint ~* '(\\.php|/wp-|/\\.env|/\\.git|/etc/passwd|win\\.ini|phpmyadmin|xmlrpc|/\\.aws|/\\.ssh|/cgi-bin)'
       OR r.user_agent ~ '(OR [0-9]+\\*[0-9]+|\\$\\{@print|<script|\\bunion\\b.*\\bselect\\b)'
    THEN 'scanner'
  WHEN r.user_agent ~* '(bot[/ ]|bot$|crawler|spider|slurp|googlebot|bingbot|gptbot|claudebot|ccbot|amazonbot|perplexity|applebot|yandex|baiduspider|semrush|ahrefs|bytespider|facebookexternalhit)'
    THEN 'crawler'
  WHEN r.user_agent ~* '^(curl/|wget/|python-requests|python-httpx|python-urllib|aiohttp|go-http-client|axios/|node-fetch|okhttp|java/|libwww-perl|guzzle)'
       OR r.user_agent ~* '(python-requests|python-httpx|go-http-client|okhttp)'
    THEN 'library'
  WHEN r.user_agent LIKE 'Mozilla/%'
    THEN 'browser'
  WHEN r.user_agent IS NULL OR r.user_agent = ''
    THEN 'empty-ua'
  ELSE 'unknown'
END
"""

VALID_TRAFFIC_CLASSES = frozenset(
    {"self", "monitor", "scanner", "bulk", "crawler", "library", "browser", "empty-ua", "unknown"}
)

# A single /24 issuing more than this in one day is not somebody reading the
# site. The first rollup put 62,772 requests from the 2026-08-29 scan into
# "browser" because that scanner walked ordinary paths behind a Chrome
# user-agent and the per-row fingerprints had nothing to catch it on. Volume is
# what gives it away, and volume is not visible in a single row.
BULK_REQUESTS_PER_DAY = 5000

# The marker below is replaced with TRAFFIC_CLASS_SQL at import; the day window
# is bound as $1. A literal replace is used rather than % or .format() so the
# LIKE patterns inside the CASE need no escaping and the exported constant stays
# valid SQL on its own.
_USAGE_DAILY_TEMPLATE = """
INSERT INTO usage_daily
    (day, endpoint_key, status_code, source, traffic_class,
     requests, distinct_ips, distinct_dids)
SELECT
    r.ts::date                        AS day,
    left(coalesce(r.endpoint, ''), 120) AS endpoint_key,
    r.status_code,
    coalesce(r.source, 'unknown')     AS source,
    __TRAFFIC_CLASS__                 AS traffic_class,
    count(*)                          AS requests,
    count(DISTINCT r.ip)              AS distinct_ips,
    count(DISTINCT r.agent_did)       AS distinct_dids
FROM request_log r
JOIN (
    SELECT ip AS bulk_ip, ts::date AS bulk_day, count(*) AS requests_that_day
    FROM request_log
    WHERE ts >= (CURRENT_DATE - $1::int)
    GROUP BY 1, 2
) ipday ON ipday.bulk_ip IS NOT DISTINCT FROM r.ip AND ipday.bulk_day = r.ts::date
WHERE r.ts >= (CURRENT_DATE - $1::int)
GROUP BY 1, 2, 3, 4, 5
ON CONFLICT (day, endpoint_key, status_code, source, traffic_class)
DO UPDATE SET
    requests      = EXCLUDED.requests,
    distinct_ips  = EXCLUDED.distinct_ips,
    distinct_dids = EXCLUDED.distinct_dids,
    rolled_up_at  = now()
"""

# Settlement side. The 402 count comes from the request log; the settled count
# comes from payment_events, because a 200 on a priced endpoint proves only that
# the gate opened, not that money moved.
_USAGE_PAYMENTS_SQL = """
INSERT INTO usage_daily_payments
    (day, challenges_402, settled_payments, distinct_wallets, usdc_total)
SELECT
    d.day,
    coalesce(r.challenges, 0),
    coalesce(p.settled, 0),
    coalesce(p.wallets, 0),
    coalesce(p.total, 0)
FROM (
    SELECT ts::date AS day FROM request_log WHERE ts >= (CURRENT_DATE - $1::int)
    UNION
    SELECT received_at::date FROM payment_events WHERE received_at >= (CURRENT_DATE - $1::int)
) d
LEFT JOIN (
    SELECT ts::date AS day, count(*) AS challenges
    FROM request_log
    WHERE status_code = 402 AND ts >= (CURRENT_DATE - $1::int)
    GROUP BY 1
) r ON r.day = d.day
LEFT JOIN (
    SELECT received_at::date AS day,
           count(*) AS settled,
           count(DISTINCT from_address) AS wallets,
           coalesce(sum(amount_usdc), 0) AS total
    FROM payment_events
    WHERE received_at >= (CURRENT_DATE - $1::int)
    GROUP BY 1
) p ON p.day = d.day
ON CONFLICT (day) DO UPDATE SET
    challenges_402   = EXCLUDED.challenges_402,
    settled_payments = EXCLUDED.settled_payments,
    distinct_wallets = EXCLUDED.distinct_wallets,
    usdc_total       = EXCLUDED.usdc_total,
    rolled_up_at     = now()
"""

# Built once at import. The substituted fragment is the module constant above,
# never anything reachable from a request.
USAGE_DAILY_SQL = _USAGE_DAILY_TEMPLATE.replace(
    "__TRAFFIC_CLASS__",
    TRAFFIC_CLASS_SQL.replace("__BULK_THRESHOLD__", str(BULK_REQUESTS_PER_DAY)),
)

# Identifiers cannot be bound as parameters, so the prune targets come from this
# literal tuple and nowhere else.
_ROLLUP_TABLES = ("usage_daily", "usage_daily_payments", "usage_daily_keys")
_PRUNE_SQL = {
    table: f"DELETE FROM {table} WHERE day < (CURRENT_DATE - ($1::int * INTERVAL '1 month'))"  # nosec B608 - name from _ROLLUP_TABLES
    for table in _ROLLUP_TABLES
}


async def rollup_days(conn, days_back: int = 31) -> int:
    """Aggregate request_log into usage_daily for every day it still holds.

    Idempotent: re-running replaces the counters for the days it covers, so a
    missed run self-heals on the next one and a partially-logged day is
    corrected once it is complete.

    Returns the number of days written.
    """
    await conn.execute(USAGE_DAILY_SQL, days_back)
    await conn.execute(_USAGE_PAYMENTS_SQL, days_back)
    return await conn.fetchval(
        "SELECT count(DISTINCT day) FROM usage_daily WHERE day >= (CURRENT_DATE - $1::int)",
        days_back,
    )


async def prune_rollups(conn, months: int = ROLLUP_RETENTION_MONTHS) -> int:
    """Drop rollup rows older than the retention window. Returns rows deleted."""
    deleted = 0
    for table in _ROLLUP_TABLES:
        result = await conn.execute(_PRUNE_SQL[table], months)
        deleted += int(result.split()[-1]) if result else 0
    return deleted
