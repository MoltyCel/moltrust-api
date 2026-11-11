"""Ref-guard attempts for the 08:00 report (since 2026-10-11).

ops/deploy/ref-guard-hook logs every change to a deployed checkout's HEAD,
main or origin/main that did not come from deploy.sh, to
~/selftest/ref-guard.jsonl. Until 2026-10-18 it only logs; from then on it
refuses (Lars, BJ1). Each attempt of the last 24 hours is named with date and
path, logged or refused alike.
"""
from __future__ import annotations

import datetime as dt
import json
import os

LOG = "~/selftest/ref-guard.jsonl"
WINDOW_HOURS = 24
MAX_ITEMS = 8


def lines(now: dt.datetime | None = None, path: str = LOG) -> list[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=WINDOW_HOURS)
    rows = []
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                    t = dt.datetime.fromisoformat(r["ts"])
                except (ValueError, KeyError):
                    continue
                if since <= t < now:
                    rows.append((t, r))
    except FileNotFoundError:
        pass
    refused = sum(1 for _, r in rows if r.get("mode") == "refuse")
    head = (f"Ref-Wache ({WINDOW_HOURS} h, ab 18.10. ablehnend): {len(rows)} Eingriff(e)"
            + (f", {refused} abgelehnt" if refused else ""))
    items = [f"  {t:%d.%m. %H:%M}Z {r.get('mode')} {r.get('cwd', '?')}: {r.get('refs', '')}"
             f" — {(r.get('action') or r.get('cmd') or '?')[:80]}" for t, r in rows[-MAX_ITEMS:]]
    return [head] + items


if __name__ == "__main__":
    print("\n".join(lines()))
