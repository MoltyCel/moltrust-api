#!/usr/bin/env python3
"""ops/crontab.txt says what the server runs. It has to be true.

The file's own header reads "authoritative snapshot" and "Apply with: crontab
ops/crontab.txt". On 2026-10-03 it was last touched on 2026-07-21 and had
drifted by 38 lines: the registry-proof publish, wallet_reconcile, gate_measure,
the JWKS refresh, logrotate and 33 others ran on the server and stood nowhere in
the file, while 14 lines in the file ran nowhere. Applying it as instructed
would have deleted all 38.

A snapshot nobody compares is worse than no snapshot, because it invites that
command. So this compares, and the invariant runs it hourly.

Environment assignments and comments are ignored; only schedule lines count,
compared as whitespace-collapsed strings so reformatting is not a finding.

One exception, on the declared side only: a one-shot line (fixed minute, hour,
day and month, any weekday) whose time has passed may be missing from the live
crontab. Such a line may remove itself after it ran (ops CJ7, 18.10.2026), and
a finding for a job that is meant to be gone would keep this check red until
someone edits the snapshot by hand. A one-shot line that still runs live after
its date is still compared like any other.
"""
import datetime
import os
import pathlib
import re
import subprocess
import sys

SNAPSHOT = pathlib.Path(__file__).resolve().parents[1] / "ops" / "crontab.txt"
ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def jobs(text):
    out = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or ENV_LINE.match(s):
            continue
        out.add(" ".join(s.split()))
    return out


def expired_one_shot(job, now):
    """True for a one-shot schedule whose time this year lies before now (UTC)."""
    f = job.split()
    if len(f) < 6 or f[4] != "*" or not all(x.isdigit() for x in f[:4]):
        return False
    try:
        due = datetime.datetime(now.year, int(f[3]), int(f[2]), int(f[1]),
                                int(f[0]), tzinfo=datetime.timezone.utc)
    except ValueError:
        return False
    return due < now


def main():
    try:
        p = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                           timeout=60)
    except OSError as exc:
        print(f"UNREADABLE crontab: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(-1)
        return 2
    if p.returncode != 0:
        print(f"UNREADABLE crontab -l: exit {p.returncode}", file=sys.stderr)
        print(-1)
        return 2
    if not SNAPSHOT.exists():
        print(f"UNREADABLE snapshot: {SNAPSHOT} is gone", file=sys.stderr)
        print(-1)
        return 2

    live, snap = jobs(p.stdout), jobs(SNAPSHOT.read_text())
    only_live = sorted(live - snap)
    now = datetime.datetime.now(datetime.timezone.utc)
    only_snap = sorted(j for j in snap - live if not expired_one_shot(j, now))
    for j in only_live:
        print(f"RUNS, NOT DECLARED: {j[:160]}", file=sys.stderr)
    for j in only_snap:
        print(f"DECLARED, NOT RUNNING: {j[:160]}", file=sys.stderr)
    print(f"{len(live)} live, {len(snap)} declared", file=sys.stderr)
    print(len(only_live) + len(only_snap))
    return 1 if (only_live or only_snap) else 0


if __name__ == "__main__":
    sys.exit(main())
