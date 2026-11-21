"""Growth lines for the digests (ops WM-3): origin groups, forecast, bounty.

Read-only. Every figure comes from one psql call per query against the live
database; nothing is written, nothing is sent. Two modes:

    python3 scripts/wm3_growth.py            three lines for daily_stats.sh
    python3 scripts/wm3_growth.py --weekly   the Monday block for weekly_traffic.sh

The lines:

1. New registrations in the last 7 days per origin group. The groups are the
   buckets of app/sql/registry_export.sql (bounty, partner, organic, usage,
   own_test), read from that file so the digest and the registry export cannot
   name a platform differently. A platform the export does not list is shown
   as `other` rather than dropped; one that starts with `usage-` is `usage`,
   so a new client value is counted before the export learns it. System agents
   are own_test whatever their platform.
2. A forecast date for registered = 1,000: linear on the trailing 14-day
   registration rate, without platform test and system agents. `registered`
   is the definition of app/sql/agent_counts.sql (not revoked). Rate 0 prints
   n/a; a target already reached prints "reached".
3. The WM-3 bounty: cost per valid agent and error rate.

   Cost: pool_spend rows with purpose prefix `wm3-bounty`, state spent. Every
   payout of the round books under that prefix.
   Cohort: registrations whose agent_source.source starts with `wm3-bounty`
   (the task text tells the agent to pass source=wm3-bounty-<round>).
   Valid: counts as registered (not revoked), platform not test, not a system
   agent, not partner_test by the agent_counts.sql name heuristic, and at least
   one API-key call (usage_daily_keys) within 7 days of registration.
   Error rate: of the cohort registrations whose 7-day window has closed, the
   share that did not become valid. A registration attempt that failed before
   it wrote a row leaves nothing in the database to count; those show up only
   as fewer cohort rows, so the rate is a floor for "never became valid", not a
   measure of request errors. Registrations still inside their window are shown
   as pending and are in neither numerator nor denominator.
   Until a round runs: cost 0, valid 0, cost per valid n/a, error rate n/a.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPORT_SQL = ROOT / "app" / "sql" / "registry_export.sql"

TARGET = 1000
RATE_DAYS = 14
BOUNTY_PREFIX = "wm3-bounty"
GROUP_ORDER = ("usage", "organic", "bounty", "partner", "own_test", "other")

Q_GROUPS_7D = """
SELECT COALESCE(platform, ''), COALESCE(agent_type, ''), COUNT(*)
  FROM agents
 WHERE revoked_at IS NULL AND created_at > now() - interval '7 days'
 GROUP BY 1, 2"""

# The previous ISO week, Monday 00:00 to Monday 00:00 UTC.
Q_GROUPS_LAST_WEEK = """
SELECT COALESCE(platform, ''), COALESCE(agent_type, ''), COUNT(*)
  FROM agents
 WHERE revoked_at IS NULL
   AND created_at >= date_trunc('week', now() AT TIME ZONE 'UTC') - interval '7 days'
   AND created_at <  date_trunc('week', now() AT TIME ZONE 'UTC')
 GROUP BY 1, 2"""

Q_REGISTERED = "SELECT COUNT(*) FROM agents WHERE revoked_at IS NULL"

# Literal SQL throughout: the 14 below is RATE_DAYS, the prefix BOUNTY_PREFIX;
# tests/test_wm3_growth.py checks the two agree.
Q_RATE = """
SELECT COUNT(*) FROM agents
 WHERE revoked_at IS NULL
   AND created_at > now() - interval '14 days'
   AND platform IS DISTINCT FROM 'test'
   AND agent_type IS DISTINCT FROM 'system'"""

Q_BOUNTY_COST = """
SELECT COALESCE(SUM(usdc), 0) FROM pool_spend
 WHERE purpose LIKE 'wm3-bounty%' AND state = 'spent'"""

Q_BOUNTY_COHORT = """
WITH cohort AS (
  SELECT a.did, a.created_at,
         (a.revoked_at IS NULL
          AND a.platform IS DISTINCT FROM 'test'
          AND a.agent_type IS DISTINCT FROM 'system'
          AND NOT (lower(a.display_name) LIKE '%test%'
                OR lower(a.display_name) LIKE '%probe%'
                OR lower(a.display_name) LIKE '%ambassador%')
          AND EXISTS (SELECT 1 FROM usage_daily_keys k
                       WHERE k.did = a.did
                         AND k.day >= a.created_at::date
                         AND k.day <= (a.created_at + interval '7 days')::date)) AS valid,
         a.created_at <= now() - interval '7 days' AS matured
    FROM agents a JOIN agent_source s ON s.did = a.did
   WHERE s.source LIKE 'wm3-bounty%'
)
SELECT COUNT(*) FILTER (WHERE valid),
       COUNT(*) FILTER (WHERE matured),
       COUNT(*) FILTER (WHERE matured AND NOT valid),
       COUNT(*) FILTER (WHERE NOT matured AND NOT valid)
  FROM cohort"""


# --- pure helpers ------------------------------------------------------------

def bucket_map(sql_text: str) -> dict[str, str]:
    """platform -> bucket, from the bucket_map VALUES of registry_export.sql."""
    body = sql_text.split("bucket_map(platform, bucket) AS (VALUES", 1)[1].split("\n),", 1)[0]
    return dict(re.findall(r"\('([^']*)','([^']*)'\)", body))


def origin_group(platform: str, agent_type: str, bmap: dict[str, str]) -> str:
    if agent_type == "system":
        return "own_test"
    p = (platform or "").strip().lower()
    if p in bmap:
        return bmap[p]
    if p.startswith("usage-"):
        return "usage"
    return "other"


def group_counts(rows, bmap) -> dict[str, int]:
    out: dict[str, int] = {}
    for platform, agent_type, n in rows:
        g = origin_group(platform, agent_type, bmap)
        out[g] = out.get(g, 0) + int(n)
    return out


def format_groups(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    order = [g for g in GROUP_ORDER if g in counts] + sorted(set(counts) - set(GROUP_ORDER))
    parts = ", ".join(f"{g} {counts[g]}" for g in order)
    return f"+{total}" + (f" ({parts})" if parts else "")


def forecast(registered: int, new_in_window: int, today: _dt.date,
             target: int = TARGET, days: int = RATE_DAYS) -> str:
    if registered >= target:
        return "reached"
    if new_in_window <= 0:
        return "n/a"
    rate = new_in_window / days
    return (today + _dt.timedelta(days=math.ceil((target - registered) / rate))).isoformat()


def bounty_line(cost: float, valid: int, matured: int, failed: int, pending: int) -> str:
    per_valid = f"{cost / valid:.4f} USDC" if valid else "n/a"
    rate = f"{100 * failed / matured:.0f}% ({failed}/{matured})" if matured else "n/a"
    return (f"WM-3 bounty: cost {cost:.2f} USDC, valid {valid}, cost/valid {per_valid}, "
            f"error rate {rate}, pending {pending}")


# --- database ----------------------------------------------------------------

def psql_rows(sql: str) -> list[list[str]]:
    """One query through psql, rows split on '|'. Raises on a failed call."""
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", os.environ.get("DB_USER", "moltstack"),
         "-d", os.environ.get("DB_NAME", "moltstack"), "-X", "-q", "-t", "-A",
         "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=True, capture_output=True, text=True).stdout
    return [line.split("|") for line in out.splitlines() if line.strip()]


def _scalar(run, sql):
    rows = run(sql)
    return rows[0][0] if rows else "0"


def daily_lines(run=psql_rows, today: _dt.date | None = None) -> list[str]:
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    bmap = bucket_map(EXPORT_SQL.read_text())
    groups = group_counts(run(Q_GROUPS_7D), bmap)
    registered = int(_scalar(run, Q_REGISTERED))
    rate_n = int(_scalar(run, Q_RATE))
    cost = float(_scalar(run, Q_BOUNTY_COST))
    valid, matured, failed, pending = (int(x) for x in run(Q_BOUNTY_COHORT)[0])
    return [
        f"New registrations 7d by origin: {format_groups(groups)}",
        f"Forecast registered {TARGET}: {forecast(registered, rate_n, today)} "
        f"(registered {registered}, {rate_n} in {RATE_DAYS}d without test/system)",
        bounty_line(cost, valid, matured, failed, pending),
    ]


def weekly_lines(run=psql_rows, today: _dt.date | None = None) -> list[str]:
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    bmap = bucket_map(EXPORT_SQL.read_text())
    groups = group_counts(run(Q_GROUPS_LAST_WEEK), bmap)
    registered = int(_scalar(run, Q_REGISTERED))
    rate_n = int(_scalar(run, Q_RATE))
    monday = today - _dt.timedelta(days=today.weekday())
    week = (monday - _dt.timedelta(days=7)).isocalendar()
    return [
        f"New registrations week {week[0]}-W{week[1]:02d} by origin: {format_groups(groups)}",
        f"Forecast registered {TARGET}: {forecast(registered, rate_n, today)} "
        f"(registered {registered}, {rate_n} in {RATE_DAYS}d without test/system)",
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--weekly", action="store_true")
    args = ap.parse_args(argv)
    try:
        lines = weekly_lines() if args.weekly else daily_lines()
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError, ValueError) as e:
        print(f"WM-3 growth: not available ({type(e).__name__})")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
