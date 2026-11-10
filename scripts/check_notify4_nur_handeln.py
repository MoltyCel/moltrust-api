#!/usr/bin/env python3
"""NOTIFY-4: what reaches Telegram is HANDELN, and classified.

Lars, 27./28.10.2026: Telegram only when he has to decide or do something;
everything else is filed in ~/reports/<date>.md (ops NOTIFY-4). Since the
deploy every line of ~/selftest/telegram-sent.jsonl carries `art` and `klasse`
(app/notify.py, _record_sent).

Counts the deliveries of the last 24 hours (erfolg true, no grund) that carry a
class and are either not HANDELN or of no known kind ("?", a sender outside the
repository that no pattern in app/meldungsarten.py matches). Lines without a
class are from before the deploy and are not counted. The last line is the
count; 0 holds. An unreadable log prints UNREADABLE and exits 2.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import meldungsarten, notify  # noqa: E402


def verstoesse(path: str, now: dt.datetime) -> list[dict]:
    ab = now - dt.timedelta(hours=24)
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            try:
                z = json.loads(raw)
                ts = dt.datetime.fromisoformat(z["ts"])
            except (ValueError, KeyError):
                continue
            if ts < ab or "klasse" not in z or not z.get("erfolg") or z.get("grund"):
                continue
            if z["klasse"] != meldungsarten.HANDELN or z.get("art") == meldungsarten.UNBEKANNT:
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
        print(f"{z['ts']} {z['kanal']} {z.get('art')}/{z.get('klasse')}: "
              f"{z.get('text_anfang', '')[:60]!r}")
    print(len(v))
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
