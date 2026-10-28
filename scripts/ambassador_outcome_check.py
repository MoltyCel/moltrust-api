#!/usr/bin/env python3
"""Ambassador runs that found posts and gave no outcome for some of them.

Guard for ops AMB-NULL (28.10.2026). From 27.10. every reply run logged
"Found 10 posts" and then "0 replies" with all skip counters at zero. Since
AMB-NULL each run closes with an Outcomes line whose accounted figure equals the
posts found; a run without it, or with a smaller figure, left posts without a
reason. The expected count is zero.

    python3 scripts/ambassador_outcome_check.py [--hours 2] [LOG]

Prints "ambassador-unaccounted=<n> runs=<m>" and exits 1 when n > 0. When no run
in the window read any posts it prints UNREADABLE and exits 2, so a stopped
ambassador never reads as a pass.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents import ambassador_outcomes as ao  # noqa: E402
from app import rotated_logs  # noqa: E402

DEFAULT_LOG = os.path.expanduser("~/moltstack/logs/ambassador.log")
TAIL = 20000


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2)
    ap.add_argument("log", nargs="?", default=DEFAULT_LOG)
    a = ap.parse_args(argv)
    window = ao.in_window(ao.runs(rotated_logs.tail_lines(a.log, TAIL)),
                          datetime.now(timezone.utc), a.hours)
    measured = [r for r in window if r["found"]]
    if not measured:
        # Not "0 unaccounted" — nothing was measured. The expected value must
        # not be printed: scripts/selftest.py keeps stdout and drops the exit
        # code whenever stdout is non-empty (see c-listwatch-no-silent-skip).
        print(f"UNREADABLE: kein Ambassador-Lauf mit gefundenen Beiträgen in {a.hours:g} h")
        return 2
    bad = ao.unaccounted(measured)
    for r in bad:
        print(f"{r['at']:%Y-%m-%dT%H:%M}Z found={r['found']} accounted={r['accounted']}")
    print(f"ambassador-unaccounted={len(bad)} runs={len(measured)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
