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
        key = f"{method} {path}"

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
  WHEN ip = '127.0.0.1' OR ip LIKE '46.225.175.%' OR ip LIKE '10.%'
       OR ip LIKE '192.168.%' OR ip LIKE '172.16.%'
    THEN 'self'
  WHEN user_agent ~* '(uptime-kuma|upptime|uptimerobot|pingdom|statuscake|payforapi-health|a2a-registry-healthcheck|gold-402-verifier|terminus-observatory|blackbox_exporter|newrelic|datadog)'
    THEN 'monitor'
  WHEN user_agent ~* '(sqlmap|nikto|nuclei|feroxbuster|masscan|zgrab|censys|shodan|internetmeasurement|expanse|dirbuster|gobuster|wpscan)'
       OR endpoint ~* '(\\.php|/wp-|/\\.env|/\\.git|/etc/passwd|win\\.ini|phpmyadmin|xmlrpc|/\\.aws|/\\.ssh|/cgi-bin)'
       OR user_agent ~ '(OR [0-9]+\\*[0-9]+|\\$\\{@print|<script|\\bunion\\b.*\\bselect\\b)'
    THEN 'scanner'
  WHEN user_agent ~* '(bot[/ ]|bot$|crawler|spider|slurp|googlebot|bingbot|gptbot|claudebot|ccbot|amazonbot|perplexity|applebot|yandex|baiduspider|semrush|ahrefs|bytespider|facebookexternalhit)'
    THEN 'crawler'
  WHEN user_agent ~* '^(curl/|wget/|python-requests|python-httpx|python-urllib|aiohttp|go-http-client|axios/|node-fetch|okhttp|java/|libwww-perl|guzzle)'
       OR user_agent ~* '(python-requests|python-httpx|go-http-client|okhttp)'
    THEN 'library'
  WHEN user_agent LIKE 'Mozilla/%'
    THEN 'browser'
  WHEN user_agent IS NULL OR user_agent = ''
    THEN 'empty-ua'
  ELSE 'unknown'
END
"""

VALID_TRAFFIC_CLASSES = frozenset(
    {"self", "monitor", "scanner", "crawler", "library", "browser", "empty-ua", "unknown"}
)


async def rollup_days(conn, days_back: int = 31) -> int:
    """Aggregate request_log into usage_daily for every day it still holds.

    Idempotent: re-running replaces the counters for the days it covers, so a
    missed run self-heals on the next one and a partially-logged day is
    corrected once it is complete.

    Returns the number of days written.
    """
    # The only interpolation is TRAFFIC_CLASS_SQL, a module constant defined
    # above. The day window is a bound parameter.
    await conn.execute(  # nosec B608 - interpolated fragment is a module constant, not input
        f"""
        INSERT INTO usage_daily
            (day, endpoint_key, status_code, source, traffic_class,
             requests, distinct_ips, distinct_dids)
        SELECT
            ts::date                                   AS day,
            left(coalesce(endpoint, ''), 120)          AS endpoint_key,
            status_code,
            coalesce(source, 'unknown')                AS source,
            {TRAFFIC_CLASS_SQL}                        AS traffic_class,
            count(*)                                   AS requests,
            count(DISTINCT ip)                         AS distinct_ips,
            count(DISTINCT agent_did)                  AS distinct_dids
        FROM request_log
        WHERE ts >= (CURRENT_DATE - $1::int)
        GROUP BY 1, 2, 3, 4, 5
        ON CONFLICT (day, endpoint_key, status_code, source, traffic_class)
        DO UPDATE SET
            requests      = EXCLUDED.requests,
            distinct_ips  = EXCLUDED.distinct_ips,
            distinct_dids = EXCLUDED.distinct_dids,
            rolled_up_at  = now()
        """,
        days_back,
    )

    # Settlement side. The 402 count comes from the request log; the settled
    # count comes from payment_events, because a 200 on a priced endpoint only
    # proves the gate opened, not that money moved.
    await conn.execute(
        """
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
        """,
        days_back,
    )

    return await conn.fetchval(
        "SELECT count(DISTINCT day) FROM usage_daily WHERE day >= (CURRENT_DATE - $1::int)",
        days_back,
    )


async def prune_rollups(conn, months: int = ROLLUP_RETENTION_MONTHS) -> int:
    """Drop rollup rows older than the retention window. Returns rows deleted."""
    deleted = 0
    # Identifiers cannot be bound as parameters, so they come from this literal
    # tuple and nowhere else. Nothing here is reachable from a request.
    for table, column in (
        ("usage_daily", "day"),
        ("usage_daily_payments", "day"),
        ("usage_daily_keys", "day"),
    ):
        result = await conn.execute(  # nosec B608 - table/column from the literal tuple above
            f"DELETE FROM {table} WHERE {column} < (CURRENT_DATE - ($1::int * INTERVAL '1 month'))",
            months,
        )
        deleted += int(result.split()[-1]) if result else 0
    return deleted
