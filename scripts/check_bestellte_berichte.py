#!/usr/bin/env python3
"""MELD-BERICHT: the reports Lars ordered have reached him in the last 24 hours.

From 07.11.2026 11:26:44Z to 12.11.2026 News Scout and Content-Scout were
filed instead of sent: NOTIFY-4 (#1032) put their kind `scout-bericht` into
ABLAGE, and nothing noticed, because a filed report looks like a quiet day.
Market Watch (agents/scout.py) had never sent at all. Lars noticed after five
days (ops MELD-BERICHT).

Reads ~/selftest/telegram-sent.jsonl (app/notify.py, _record_sent) and counts,
per report, the lines of the last 24 hours with art `scout-bericht` whose first
characters name the report and that reached Telegram: `erfolg` true, or
throttled (a throttled repeat was delivered once within the hour). The
schedule is the server crontab: Market Watch (agents/scout.py) twice a day
(08:00 and 18:00 UTC), News Scout once (17:00 UTC), Content-Scout once. The
Content-Scout runs at 06:30 and 17:30 UTC, but the 17:30 run repeats the
06:30 summary and has been throttled since before 07.11.; the coordinator
decided on 12.11.2026 that the throttle stays, so one delivery counts.
Prints one line per report short of its count and, last, the number of such
reports; 0 holds. An unreadable log prints UNREADABLE and exits 2.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import notify  # noqa: E402

ART = "scout-bericht"
# report: (words in its first line, deliveries expected in 24 hours)
BERICHTE = {
    "Market Watch": ("Market Watch —", 2),
    "News Scout": ("MolTrust News Scout", 1),
    "Content-Scout": ("Content-Scout:", 1),
}


def zaehlen(path: str, now: dt.datetime) -> dict[str, int]:
    ab = now - dt.timedelta(hours=24)
    n = {name: 0 for name in BERICHTE}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            try:
                z = json.loads(raw)
                ts = dt.datetime.fromisoformat(z["ts"])
            except (ValueError, KeyError):
                continue
            if ts < ab or z.get("art") != ART:
                continue
            if not (z.get("erfolg") or z.get("grund") == "gedrosselt"):
                continue
            anfang = z.get("text_anfang", "")
            for name, (worte, _soll) in BERICHTE.items():
                if worte in anfang:
                    n[name] += 1
    return n


def main(path: str | None = None, now: dt.datetime | None = None) -> int:
    path = path or notify.SENT_LOG
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        n = zaehlen(path, now)
    except OSError as exc:
        print(f"UNREADABLE: {path}: {type(exc).__name__}")
        return 2
    fehlend = [name for name, (_w, soll) in BERICHTE.items() if n[name] < soll]
    for name in fehlend:
        print(f"{name}: {n[name]} of {BERICHTE[name][1]} delivered in 24 h")
    print(len(fehlend))
    return 1 if fehlend else 0


if __name__ == "__main__":
    sys.exit(main())
