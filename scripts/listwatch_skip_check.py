#!/usr/bin/env python3
"""ListWatch runs that skipped a month index and still said "nothing new".

Guard for ops L7 (18.10.2026). scripts/w3c_listwatch.py logs a month it could
not fetch as "HTTP <status>, skipping this month". Until L7 such a run could end
with "nothing new", so an archive answering 429 looked like a quiet list. A run
is the stretch of log lines up to and including its closing line ("report
sent", "nothing new", "seeded", or the send failure). The check counts runs in
the window that contain a skip and close with "nothing new"; the expected count
is zero.

    python3 scripts/listwatch_skip_check.py [--hours 48] [LOG ...]

Prints "listwatch-silent-skip=<n> runs=<m>". Exit 2 when no log was readable or
no run closed inside the window, so a missing log never reads as a pass.
"""
import argparse
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\] (\w+): (.*)$")
SKIP = "skipping this month"
CLOSERS = ("report sent", "nothing new", "seeded", "Telegram send failed")
DEFAULT_LOG = os.path.expanduser("~/moltstack/logs/w3c_listwatch.log")


def runs(lines):
    """[(closing time, closing message, skipped?)] in log order."""
    out, skipped = [], False
    for line in lines:
        m = LINE_RE.match(line.rstrip("\n"))
        if not m:
            continue
        ts, _level, msg = m.groups()
        if SKIP in msg:
            skipped = True
        if msg.startswith(CLOSERS):
            when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            out.append((when, msg, skipped))
            skipped = False
    return out


def silent_skips(lines, now, hours):
    cut = now - timedelta(hours=hours)
    window = [r for r in runs(lines) if r[0] > cut]
    bad = [r for r in window if r[2] and r[1].startswith("nothing new")]
    return len(bad), len(window)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("logs", nargs="*")
    a = ap.parse_args(argv)
    paths = a.logs or sorted(glob.glob(DEFAULT_LOG + "*"))
    lines = []
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines.extend(f.readlines())
        except OSError:
            continue
    # Rotated files carry older runs; order by the timestamp each line starts with.
    lines.sort(key=lambda l: l[:22])
    bad, total = silent_skips(lines, datetime.now(timezone.utc), a.hours)
    print(f"listwatch-silent-skip={bad} runs={total}")
    if total == 0:
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
