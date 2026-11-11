#!/usr/bin/env python3
"""DISC-1: the discovery run sends no counters to Telegram.

Until 31.10.2026 every discovery run with a new thread sent
"MolTrust Discovery: N new, N pruned, N total" plus five titles, mostly
coordination channels (ops DISC-1). scripts/discovery.py now sends only threads
with a recommendation for Lars, and never the counter line.

Counts the delivered sends (erfolg true) in the last 24 hours, not before
START, whose text start carries the counter line. The last line is that count;
0 holds. An unreadable log prints UNREADABLE and exits 2.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import notify  # noqa: E402

# First discovery run after the deploy is 01.11.2026 06:00Z.
START = dt.datetime(2026, 11, 1, 5, 0, tzinfo=dt.timezone.utc)
ZAEHLER = re.compile(r"Discovery: \d+ new, \d+ pruned")


def verstoesse(path: str, now: dt.datetime) -> list[dict]:
    ab = max(START, now - dt.timedelta(hours=24))
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            try:
                z = json.loads(raw)
                ts = dt.datetime.fromisoformat(z["ts"])
            except (ValueError, KeyError):
                continue
            if ts >= ab and z.get("erfolg") and ZAEHLER.search(str(z.get("text_anfang") or "")):
                out.append(z)
    return out


def main(path: str | None = None, now: dt.datetime | None = None) -> int:
    path = path or notify.SENT_LOG
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        v = verstoesse(path, now)
    except OSError as exc:
        print(f"UNREADABLE: {path}: {type(exc).__name__}")
        return 2
    for z in v:
        print(f"{z['ts']} {z['kanal']}: {z.get('text_anfang', '')[:60]!r}")
    print(len(v))
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
