#!/usr/bin/env python3
"""MELD-1: no report without a step for Lars reaches Telegram.

Lars, 27.10.2026: Telegram only when he has to decide or do something. A report
whose first line is "Lars muss: nichts" stays in its detail file. On 28.10. and
29.10.2026 the selftest digest went out on STATS at 20:00 with exactly that first
line, two nights in a row, while the rule stood (ops MELD-1 criterion 5).

Counts the delivered sends (erfolg true) on STATS and WORKLOG in the last 24
hours, not before START, whose text begins with notify.KEIN_HANDGRIFF. The last
line is that count; 0 holds. An unreadable log prints UNREADABLE and exits 2.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import notify  # noqa: E402

# Deploy of the fix (moltrust-api, ops MELD-1); earlier sends are the finding
# itself and already measured in the master list.
START = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.timezone.utc)
KANAELE = (notify.STATS, notify.WORKLOG)


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
            if (ts >= ab and z.get("erfolg") and z.get("kanal") in KANAELE
                    and str(z.get("text_anfang") or "").startswith(notify.KEIN_HANDGRIFF)):
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
