#!/usr/bin/env python3
"""How often the Moltbook verification actually passes, before and after a fix.

    python3 scripts/moltbook_verify_rate.py
    python3 scripts/moltbook_verify_rate.py --since 2026-09-20T12:59 --json

Issue #372 asks to re-measure the failure rate after #371 before choosing
between the two remaining fixes. Nothing measured it, so the choice was going
to be made on whatever the last run happened to do — which on 2026-09-20 was a
single failure, n=1.

The success line reads "Verification passed". It is worth saying so here,
because grepping for the plausible-sounding "Verification succeeded" returns
zero matches and makes a 31.5% failure rate look like 100%. That mistake was
made once already while writing this.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_LOG = Path.home() / "moltstack" / "logs" / "moltbook.log"

PASS_MARKER = "Verification passed"
FAIL_MARKER = "Verification failed"
TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def events(log: Path, since: str | None):
    """(timestamp, passed) for every verification outcome, oldest first."""
    out = []
    for line in log.read_text(errors="replace").splitlines():
        passed = PASS_MARKER in line
        if not passed and FAIL_MARKER not in line:
            continue
        m = TS.match(line)
        ts = m.group(1) if m else ""
        # String comparison is correct for ISO-8601 with a fixed width, and
        # avoids a parse that would throw on the occasional malformed line.
        if since and ts <= since:
            continue
        out.append((ts, passed))
    return out


def trailing_failures(evs) -> int:
    n = 0
    for _, passed in reversed(evs):
        if passed:
            break
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--since", help="ISO timestamp; only outcomes after it")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-sample", type=int, default=10,
                    help="below this many outcomes, refuse to state a rate")
    args = ap.parse_args()

    if not args.log.exists():
        print(f"kein Log unter {args.log}", file=sys.stderr)
        return 2

    evs = events(args.log, args.since)
    total = len(evs)
    failed = sum(1 for _, p in evs if not p)
    passed = total - failed

    # A rate from three runs is not a rate. Reporting one would invite exactly
    # the decision #372 says to avoid: picking a fix from noise.
    enough = total >= args.min_sample
    result = {
        "since": args.since,
        "outcomes": total,
        "passed": passed,
        "failed": failed,
        "failure_rate_pct": round(100.0 * failed / total, 1) if total else None,
        "sample_sufficient": enough,
        "min_sample": args.min_sample,
        "trailing_failures": trailing_failures(evs),
        "first": evs[0][0] if evs else None,
        "last": evs[-1][0] if evs else None,
    }

    if args.json:
        print(json.dumps(result))
        return 0 if enough else 1

    print(f"Fenster         {args.since or 'alles'}")
    print(f"Verifikationen  {total}  ({passed} bestanden, {failed} gescheitert)")
    if total:
        print(f"Fehlerquote     {result['failure_rate_pct']}%")
        print(f"zuletzt in Folge gescheitert: {result['trailing_failures']}")
    if not enough:
        print(f"STICHPROBE ZU KLEIN — {total} von {args.min_sample}. Keine Quote behaupten.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
