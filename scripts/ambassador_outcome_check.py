#!/usr/bin/env python3
"""Ambassador runs that found posts, replied to none and gave no reason.

Guard for ops AMB-NULL (28.10.2026). From 27.10. to 28.10.2026 forty-two runs
of agents/ambassador.py logged "Found 10 posts: ... 2 foreign threads that
mention us" and then "Run done: 0 replies, 0 rate-limited, 0 low-effort,
0 off-topic, 0 gate". Both mentions had been settled in an earlier run and the
loop passed over them without a line. Since AMB-NULL every run closes with
"outcomes <settled> of <found> posts: ...", one outcome per post found.

A run is the stretch of log lines from "MOLTRUST AMBASSADOR — run" to its
"Run done:" line. It is silent when it found at least one post, wrote no
reply, and its closing line does not account for every post: no outcomes
part, settled and found differ, the parts do not add up to the found count,
or the run has no closing line at all. The expected count is zero.

    python3 scripts/ambassador_outcome_check.py [--hours 1] [LOG ...]

Prints "ambassador-silent-run=<n> runs=<m>" and exits 1 when n > 0. When no
log was readable or no run started inside the window it prints UNREADABLE and
exits 2, so a stopped agent never reads as a pass.
"""
import argparse
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\] (\w+): (.*)$")
START = "MOLTRUST AMBASSADOR — run"
FOUND_RE = re.compile(r"^Found (\d+) posts")
DONE_RE = re.compile(r"^Run done: (\d+) replies")
OUTCOMES_RE = re.compile(r"outcomes (\d+) of (\d+) posts: (.*)$")
PART_RE = re.compile(r"(?:^|, )(\d+) [a-z_]+")
DEFAULT_LOG = os.path.expanduser("~/moltstack/logs/ambassador.log")


def _stamp(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def runs(lines):
    """[(start time, found or None, closing message or None)] in log order."""
    out, cur = [], None
    for line in lines:
        m = LINE_RE.match(line.rstrip("\n"))
        if not m:
            continue
        ts, _level, msg = m.groups()
        if msg.startswith(START):
            if cur is not None:
                out.append(cur)
            cur = [_stamp(ts), None, None]
            continue
        if cur is None:
            continue
        f = FOUND_RE.match(msg)
        if f:
            cur[1] = int(f.group(1))
        elif DONE_RE.match(msg):
            cur[2] = msg
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return [tuple(r) for r in out]


def silent(found, closing):
    """True when a run found posts, replied to none and left posts unaccounted."""
    if not found:
        return False
    if closing is None:
        return True
    if int(DONE_RE.match(closing).group(1)) > 0:
        return False
    o = OUTCOMES_RE.search(closing)
    if not o:
        return True
    settled, of = int(o.group(1)), int(o.group(2))
    # The detail in brackets repeats the unconsidered count, so it is cut off.
    parts = sum(int(n) for n in PART_RE.findall(o.group(3).split(" (")[0]))
    return not (settled == of == found == parts)


def silent_runs(lines, now, hours):
    cut = now - timedelta(hours=hours)
    window = [r for r in runs(lines) if r[0] > cut]
    # A run still going at the time of the check is not judged yet.
    window = [r for r in window if r[2] is not None or r[0] < now - timedelta(minutes=10)]
    bad = [r for r in window if silent(r[1], r[2])]
    return len(bad), len(window)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1)
    ap.add_argument("logs", nargs="*")
    a = ap.parse_args(argv)
    paths = a.logs or sorted(glob.glob(DEFAULT_LOG + "*"))
    lines = []
    for p in paths:
        if p.endswith(".gz"):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines.extend(f.readlines())
        except OSError:
            continue
    # Keep the order each file was written in; only whole files are ordered,
    # because a run spans several lines and a line sort would interleave runs.
    bad, total = silent_runs(lines, datetime.now(timezone.utc), a.hours)
    if total == 0:
        print(f"UNREADABLE no ambassador run in the last {a.hours:g} h")
        return 2
    print(f"ambassador-silent-run={bad} runs={total}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
