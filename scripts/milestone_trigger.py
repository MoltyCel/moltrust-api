#!/usr/bin/env python3
"""Watch the public agent count and say when the 1,000 series has to be drafted.

The series is supposed to go out on the day the number is reached. A counter
read on that day starts it three days later, so the trigger hangs on the
projection rather than the standing figure: a rate that collapses moves the
trigger back by itself, and a rate that jumps moves it forward.

Three stages, all of them only messages:

  T-1  projection <= 7 days   write the drafts, run the voice gate   -> STATS
  T-2  projection <= 3 days   texts final, date set                  -> ALERTS
  T-3  count >= 1000 on two consecutive days                         -> ALERTS

Nothing is published here, and nothing ever will be from this file. The trigger
produces a message and, at T-1, a checklist; a human decides what goes out.

The count comes from app/sql/public_count.sql and from nowhere else, so this
file cannot drift away from the blog and the weekly proof.

    python3 scripts/milestone_trigger.py            # read, report, remember
    python3 scripts/milestone_trigger.py --dry-run  # read and print, no state
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notify  # noqa: E402 - path has to be set before the import

TARGET = 1000
T1_DAYS = 7
T2_DAYS = 3
SQL = os.path.join(os.path.dirname(__file__), "..", "app", "sql", "public_count.sql")
STATE = os.path.expanduser("~/.milestone_trigger.json")

# Checked automatically because a URL either answers or it does not. The rest of
# the gate is human judgement and is printed, not tested.
PROOF_URL = "https://moltrust.ch/registry-proof.html"
RULE_URL = "https://moltrust.ch/registry-proof.html"
RULE_MARKER = "Zählregel"

MANUAL_GATES = (
    "the bounty / partner / organic split is in the text of the first message",
    "the stricter figure is in that same message, not saved for a follow-up",
)


def psql(path: str) -> str:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-F", "\t", "-P", "pager=off", "-f", path],
        capture_output=True, text=True, timeout=180)
    if out.returncode:
        raise SystemExit(f"psql: {out.stderr[:400]}")
    return out.stdout


def section(text: str, title: str) -> list[list[str]]:
    """Rows of one '== title ==' block, header line dropped."""
    rows, grab = [], False
    for line in text.splitlines():
        if line.startswith("== "):
            grab = line.strip().strip("= ").strip() == title
            continue
        if grab and line.strip() and not line.startswith("("):
            rows.append(line.split("\t"))
    return rows[1:] if rows else []


def url_ok(url: str, marker: str | None = None) -> bool:
    try:
        with urllib.request.urlopen(  # noqa: S310  # nosec B310 - literal https
                urllib.request.Request(url, headers={"User-Agent": "MolTrust/1.0"}),
                timeout=20) as r:
            if r.status != 200:
                return False
            return marker is None or marker in r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False


def telegram(text: str, alerts: bool) -> None:
    """T-1 is a number nobody has to act on; T-2 and T-3 start a clock."""
    notify.send_telegram(text, channel=notify.ALERTS if alerts else notify.STATS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="read and print, write neither state nor telegram")
    args = ap.parse_args()

    raw = psql(os.path.abspath(SQL))

    head = section(raw, "the two headline figures")
    checks = section(raw, "completeness")
    rate_rows = section(raw, "rate7 over the counted buckets")
    if not head or not checks or not rate_rows:
        raise SystemExit("public_count.sql returned an unexpected shape; no number reported")

    count, activated, unmeasured, deducted, all_live = (int(x) for x in head[0])
    covers_all, scripted_intact, scripted_unquoted = (c == "t" for c in checks[0][:3])
    rate7 = float(rate_rows[0][0])

    # A reader that cannot prove completeness returns an error, not a number.
    if not (covers_all and scripted_intact and scripted_unquoted):
        raise SystemExit(
            f"completeness failed (buckets={covers_all} scripted_intact={scripted_intact} "
            f"unquoted={scripted_unquoted}); no number reported")

    missing = TARGET - count
    days = missing / rate7 if rate7 > 0 else float("inf")

    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except (OSError, ValueError):
            state = {}
    today = date.today().isoformat()
    at_target_yesterday = bool(state.get("at_target")) and state.get("day") != today

    stage = None
    if count >= TARGET and at_target_yesterday:
        stage = "T-3"
    elif days <= T2_DAYS:
        stage = "T-2"
    elif days <= T1_DAYS:
        stage = "T-1"

    eta = "reached" if missing <= 0 else (
        f"{days:.1f} days" if days != float("inf") else "no movement")
    body = (f"MolTrust — public count {count} of {TARGET}\n\n"
            f"still missing   {max(missing, 0)}\n"
            f"rate (7d)       {rate7:.1f}/day\n"
            f"projection      {eta}\n"
            f"activated       {activated}  (the stricter rule)\n"
            f"unmeasured      {unmeasured}  (registered before the log window)\n"
            f"deducted        {deducted}  (own and partner test agents)\n"
            f"live rows       {all_live}\n")

    if stage in ("T-1", "T-2"):
        proof = url_ok(PROOF_URL)
        rule = url_ok(RULE_URL, RULE_MARKER)
        body += (f"\n{stage}: start the drafts.\n"
                 f"  [{'x' if rule else ' '}] counting rule published\n"
                 f"  [{'x' if proof else ' '}] registry-proof page answers\n")
        for gate in MANUAL_GATES:
            body += f"  [ ] {gate}\n"
        body += "\nNothing is published by this job.\n"
    elif stage == "T-3":
        body += ("\nT-3: the count has stood at or above the target for two days.\n"
                 "Release is a human step and the gate above has to be complete.\n")

    print(body)
    if args.dry_run:
        print(f"(dry run, state at {STATE} untouched, stage {stage or 'none'})")
        return 0

    if stage:
        telegram(body, alerts=stage in ("T-2", "T-3"))

    json.dump({"day": today, "count": count, "rate7": rate7,
               "at_target": count >= TARGET, "stage": stage,
               "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
              open(STATE, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
