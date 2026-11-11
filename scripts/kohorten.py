#!/usr/bin/env python3
"""The four cohort figures per registration week (ops G6).

    python3 scripts/kohorten.py --json OUT     the block for /rounds/dauer/<KW>/result.json
    python3 scripts/kohorten.py --md OUT --since 2026-09-01
                                               the back-calculation table
    python3 scripts/kohorten.py --line         the Monday line, for the slot given by --at
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import kohorten  # noqa: E402 - path set before the import


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--md")
    ap.add_argument("--line", action="store_true")
    ap.add_argument("--since", type=dt.date.fromisoformat)
    ap.add_argument("--as-of", help="cut; default now, to the minute")
    ap.add_argument("--at", help="slot for --line, ISO UTC; default the coming Monday 08:00")
    a = ap.parse_args()

    if a.line:
        if a.at:
            at = dt.datetime.fromisoformat(a.at)
        else:
            now = dt.datetime.now(dt.timezone.utc)
            at = (now + dt.timedelta(days=(7 - now.weekday()) % 7)).replace(
                hour=8, minute=0, second=0, microsecond=0)
        print("\n".join(kohorten.digest_lines(at)))
        return 0

    b = kohorten.block(a.as_of, a.since)
    for path, text in ((a.json, json.dumps(b, ensure_ascii=False, indent=1)),
                       (a.md, kohorten.markdown(b))):
        if not path:
            continue
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
        print(f"{path}: {len(b['wochen'])} Wochen, Stichtag {b['stichtag']}")
    if not (a.json or a.md):
        print(kohorten.markdown(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
