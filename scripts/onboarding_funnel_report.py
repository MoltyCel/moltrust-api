"""Did the onboarding fix move anything? Two windows, counted from state.

On 2026-10-04 09:27 UTC the keyless path started naming where the API key
comes from (#561). The obvious way to check would be to count error codes on
/identity/bind — and it would be wrong, because the same change turned a
missing X-API-Key from 422 into 401. The codes move whether or not a single
agent gets further.

So this counts completions, not failures, and it reads them from state rather
than from the log:

  registered   a row in `agents`
  keyed        a row in `api_keys` for that DID
  bound        agents.wallet_bound_at is set

`request_log.agent_did` cannot carry this. It is populated for
/identity/bind and empty for /identity/register-pop and /auth/signup-did —
both are keyless by construction, so the middleware has nothing to attribute
the call to yet. The agents and api_keys rows are the only per-DID record of
those two steps.

Excluded: our own registrations (this host's IP and localhost), Ownify, and
57.129.0.0/16. Everything in `agents` is agent_type='external' today, so that
column is kept in the query as a guard rather than as a filter that currently
removes anything.

`platform` carries the exclusion, not the display name. The vocabulary is a
closed set written by us — `ownify` (29 rows) and `test` (27) are two of its
values — so it decides this where a name match only guesses. The
`display_name ILIKE 'ownify%'` clause stays underneath it: it catches a row
whose platform was set to something else by hand. In the 2026-09-27 baseline
week neither clause removes anything; every row is taskmarket, a2a or base.
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import notify  # noqa: E402

# The deploy of #561. Everything before is the old interface, everything
# after is the new one.
CUTOVER = "2026-10-04 09:27:00+00"

# Our own traffic. 46.225.175.218 is this host; the /24 is what the log
# anonymiser leaves. 57.129.0.0/16 is OVH and reaches us as expected internal
# traffic, not as an external agent.
EXCLUDED_IP_PREFIXES = ("46.225.175.", "127.0.0.1", "57.129.")

FUNNEL_SQL = """
SELECT count(*)                                                   AS registered,
       count(k.owner_did)                                         AS keyed,
       count(*) FILTER (WHERE a.wallet_bound_at IS NOT NULL)      AS bound
  FROM agents a
  LEFT JOIN LATERAL (SELECT owner_did FROM api_keys WHERE owner_did = a.did LIMIT 1) k ON true
 WHERE a.created_at >= %(start)s
   AND a.created_at <  %(end)s
   AND a.agent_type = 'external'
   AND coalesce(a.platform, '') NOT IN ('ownify', 'test')
   AND coalesce(a.display_name, '') NOT ILIKE 'ownify%%'
   AND coalesce(a.registration_ip, '') NOT LIKE '46.225.175.%%'
   AND coalesce(a.registration_ip, '') <> '127.0.0.1'
   AND coalesce(a.registration_ip, '') NOT LIKE '57.129.%%'
"""


def psql(sql: str, **params) -> list[list[str]]:
    binds: list[str] = []
    for name, value in params.items():
        binds += ["-v", f"{name}={value}"]
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-F", "\t", "-t", *binds, "-f", "-"],
        input=sql, capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise SystemExit(f"psql: {out.stderr[:300]}")
    return [l.split("\t") for l in out.stdout.strip().split("\n") if l.strip()]


def window(start: str, end: str) -> dict[str, int]:
    sql = FUNNEL_SQL.replace("%(start)s", ":'start'").replace("%(end)s", ":'end'").replace("%%", "%")
    rows = psql(sql, start=start, end=end)
    registered, keyed, bound = (int(x) for x in rows[0])
    return {"registered": registered, "keyed": keyed, "bound": bound}


def pct(part: int, whole: int) -> str:
    return "—" if whole == 0 else f"{100 * part / whole:.0f}%"


def delta(after: int, before: int) -> str:
    return f"{after - before:+d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--cutover", default=CUTOVER)
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()

    cut = datetime.datetime.fromisoformat(args.cutover.replace(" ", "T"))
    span = datetime.timedelta(days=args.days)
    before = window((cut - span).isoformat(sep=" "), cut.isoformat(sep=" "))
    after = window(cut.isoformat(sep=" "), (cut + span).isoformat(sep=" "))

    now = datetime.datetime.now(datetime.timezone.utc)
    if now < cut + span:
        note = (f"\n<i>The second window is still open — "
                f"{(cut + span - now).days}d {(cut + span - now).seconds // 3600}h to go.</i>")
    else:
        note = ""

    lines = [
        "<b>Onboarding funnel — before and after #561</b>",
        f"{args.days} days either side of {cut:%Y-%m-%d %H:%M} UTC.",
        "External registrations only; our own, Ownify and 57.129.0.0/16 removed.",
        "",
        "<pre>",
        f"{'':<12}{'before':>8}{'after':>8}{'delta':>8}",
        f"{'registered':<12}{before['registered']:>8}{after['registered']:>8}"
        f"{delta(after['registered'], before['registered']):>8}",
        f"{'keyed':<12}{before['keyed']:>8}{after['keyed']:>8}"
        f"{delta(after['keyed'], before['keyed']):>8}",
        f"{'bound':<12}{before['bound']:>8}{after['bound']:>8}"
        f"{delta(after['bound'], before['bound']):>8}",
        "",
        f"{'keyed/reg':<12}{pct(before['keyed'], before['registered']):>8}"
        f"{pct(after['keyed'], after['registered']):>8}",
        f"{'bound/reg':<12}{pct(before['bound'], before['registered']):>8}"
        f"{pct(after['bound'], after['registered']):>8}",
        "</pre>",
        "",
        "The two rates are the measure. Absolute counts move with how many "
        "agents happen to arrive in a week and say nothing on their own.",
        note,
    ]
    report = "\n".join(l for l in lines if l is not None)

    print(report.replace("<pre>", "").replace("</pre>", "")
          .replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", ""))

    if args.send:
        notify.send_telegram(report, channel=notify.STATS, parse_mode="HTML")
    return 0


if __name__ == "__main__":
    sys.exit(main())
