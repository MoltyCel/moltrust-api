"""Telegram messages that went out more than once, for the 08:00 report.

On 2026-10-09 the same HN submit link reached Telegram three times in seven
minutes. Whether a message repeats is visible only by counting: per message
type (notify's fingerprint, which ignores timestamps, hashes and durations)
over the last 24 hours, how often it was attempted, sent and throttled.

Reads ~/selftest/telegram-sent.jsonl, which app/notify.py writes. Messages
sent outside app/notify.py are not in it; the line says so.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os

SENT_LOG = os.path.expanduser("~/selftest/telegram-sent.jsonl")
WINDOW_HOURS = 24
MAX_ITEMS = 6


def _ts(raw: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def lines(now: dt.datetime, path: str = SENT_LOG) -> list[str]:
    if not os.path.exists(path):
        return ["Telegram-Wiederholungen: Sendeprotokoll fehlt"]
    since = now - dt.timedelta(hours=WINDOW_HOURS)
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    broken = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
                t = _ts(row["ts"])
            except (ValueError, KeyError, TypeError):
                broken += 1
                continue
            if t is None or not (since <= t < now):
                continue
            groups[str(row.get("fingerabdruck", "?"))].append(row)
    sent_total = sum(1 for rs in groups.values() for r in rs if r.get("erfolg"))
    repeated = [(fp, rs) for fp, rs in groups.items()
                if sum(1 for r in rs if r.get("erfolg")) > 1]
    head = (f"Telegram {WINDOW_HOURS} h — {len(groups)} Meldungsarten, {sent_total} gesendet, "
            f"{len(repeated)} davon mehrfach gesendet (nur ueber app/notify.py)")
    out = [head]
    repeated.sort(key=lambda x: -sum(1 for r in x[1] if r.get("erfolg")))
    for fp, rs in repeated[:MAX_ITEMS]:
        sent = sum(1 for r in rs if r.get("erfolg"))
        throttled = sum(1 for r in rs if r.get("grund") == "gedrosselt")
        text = " ".join(str(rs[0].get("text_anfang", "")).split())[:60]
        out.append(f"  {sent}x gesendet ({len(rs)} Versuche, {throttled} gedrosselt) "
                   f"[{rs[0].get('kanal', '?')}] {text}")
    if len(repeated) > MAX_ITEMS:
        out.append(f"  … und {len(repeated) - MAX_ITEMS} weitere")
    if broken:
        out.append(f"  Sendeprotokoll: {broken} Zeilen nicht lesbar")
    return out
