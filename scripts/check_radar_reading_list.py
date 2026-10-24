#!/usr/bin/env python3
"""BY5: the reply radar runs as a reading list only, from 18.10.2026.

Lars decided on 12.10.2026 (BW1, BX2) that from 18.10 the radar drafts
nothing, writes nothing to X, reads once a day at 06:05 UTC with a list page of
40, and stays under $0.25 a day. The gate is in code
(REPLY_BRANCH_RETIRED_FROM in agents/reply_radar.py); this script checks that
the gate is what actually happened, after every 06:05 run from 18.10 on.

Five checks on the latest 06:05 run that is due (due from 06:23 UTC):

  1. reading list   the run reached the end of the reading-list path: a list
                    was sent and the Telegram send log has it, or there were
                    no candidates, or the breaker had paused reads (a
                    declared quiet state, reported as such)
  2. schedule       the run read list and mentions only (no search), no run
                    started at 10:05 or 16:05 that day, and no list read
                    returned more than 40 posts
  3. drafter        no model call and no draft since 18.10 00:00 UTC
                    (log lines, and the per-day draft counter in the state)
  4. X write        no reply posted since 18.10 00:00 UTC (x_posts.jsonl, the
                    write rows of the X meter, posted_per_day in the state)
  5. run cost       the X reads inside the run's window, priced like the
                    meter, at most $0.25

Before 18.10 06:23 UTC nothing is due and the result is 0.

The last line of `main` is the number of failed checks: 0 means all four hold.
A source that cannot be read prints UNREADABLE, which the selftest runner
treats as FAIL, never as a pass. Exit 0 / 1 / 2 accordingly.

`lines()` is the Sammelmeldung's line (scripts/selftest.py --digest).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import paths  # noqa: E402

RADAR_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "agents", "reply_radar.py")
# The 06:05 run usually ends within seconds; 06:23 leaves room for a slow X.
RUN_AT = dt.time(6, 5)
DUE_AT = dt.time(6, 23)
# Reads booked within this window after the run starts are the run's reads.
RUN_WINDOW = dt.timedelta(minutes=10)
COST_CAP_USD = 0.25           # Lars, BX2: under $0.25 a day
LIST_PAGE_MAX = 40            # Lars, BX2: list page 40 from 18.10
RETIRED_RUNS = ("10:05", "16:05")   # date-guarded in the crontab from 18.10
LIST_SOURCE = "tweets"        # x_meter source of a list read (URL tail)
SENT_MARK = "Reply-Radar — Leseliste"   # first line of reading_list()
USD_PER_POST_READ = 0.005     # agents/x_meter.py
USD_PER_USER_READ = 0.010

STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]")
RUN_START = re.compile(r"REPLY RADAR — (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC")
LIST_SENT = re.compile(r"INFO:\s+reading list: (\d+) posts of (\d+) candidates sent$")
NO_CANDIDATES = re.compile(r"Candidates after filtering: 0\b")
PAUSED = re.compile(r"Reads paused:")
WINDOW_NO_SEARCH = re.compile(r"INFO: Window: .*list \+ mentions only")
# What only the drafter prints: the model's token line and a delivered draft.
DRAFTER = re.compile(r"INFO:\s+(?:tokens in=\d+|draft \d+ for )")


class Unreadable(Exception):
    pass


def retired_from() -> dt.date:
    """The date from the radar's own source, so there is one place it lives."""
    try:
        src = open(RADAR_SRC, encoding="utf-8").read()
    except OSError as exc:
        raise Unreadable(f"agents/reply_radar.py: {exc}") from exc
    m = re.search(r"REPLY_BRANCH_RETIRED_FROM\s*=\s*datetime\.date\((\d+),\s*(\d+),\s*(\d+)\)", src)
    if not m:
        raise Unreadable("REPLY_BRANCH_RETIRED_FROM not found in agents/reply_radar.py")
    return dt.date(*map(int, m.groups()))


def due_day(now: dt.datetime, start: dt.date) -> dt.date | None:
    """The latest day whose 06:05 run is due, or None before the first one."""
    day = now.date() if now.time() >= DUE_AT else now.date() - dt.timedelta(days=1)
    return day if day >= start else None


def _stamp(line: str) -> dt.datetime | None:
    m = STAMP.match(line)
    if not m:
        return None
    return dt.datetime.fromisoformat(m.group(1)).replace(tzinfo=dt.timezone.utc)


def _parse_at(value) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []        # no ledger yet means no row of that kind, not unknown
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if raw:
                    try:
                        rows.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
    except OSError as exc:
        raise Unreadable(f"{path}: {exc}") from exc
    return rows


def _log_lines(log_path: str) -> list[str]:
    """The log, after its newest rotated copy (config/logrotate.conf: dateext,
    delaycompress, so `<log>-YYYYMMDD` or `<log>-YYYYMMDD.gz`).

    The weekly rotation runs Sunday 04:00, before the 06:05 run. But selfheal
    forces one whenever /var is over 85 % (scripts/selfheal.py rotate_logs),
    and on 23.10.2026 at 16:07 that moved the morning run out of the log: the
    check read FAIL from 16:37 until the next run, for a run that had sent its
    list at 06:05.
    """
    import glob
    import gzip
    lines: list[str] = []
    rotated = sorted(glob.glob(glob.escape(log_path) + "-[0-9]*"))
    if rotated:
        newest = rotated[-1]
        opener = gzip.open if newest.endswith(".gz") else open
        with opener(newest, "rt", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    with open(log_path, encoding="utf-8", errors="replace") as f:
        return lines + f.read().splitlines()


def evaluate(day: dt.date, since: dt.datetime, log_path: str, state_path: str,
             posts_path: str, meter_path: str, sent_path: str) -> dict:
    """The five checks for the 06:05 run of `day`. Raises Unreadable."""
    try:
        log_lines = _log_lines(str(log_path))
    except OSError as exc:
        raise Unreadable(f"{log_path}: {exc}") from exc

    # 1. The run of `day` at 06:05, and how it ended.
    want = f"{day.isoformat()} {RUN_AT.strftime('%H:%M')}"
    start_idx = next((i for i, l in enumerate(log_lines)
                      if (m := RUN_START.search(l)) and m.group(1) == want), None)
    run_start = dt.datetime.combine(day, RUN_AT, dt.timezone.utc)
    block: list[str] = []
    if start_idx is None:
        listing = {"ok": False, "text": f"kein Lauf {day:%d.%m.} 06:05 im Log"}
    else:
        block = []
        for l in log_lines[start_idx + 1:]:
            if RUN_START.search(l):
                break
            t = _stamp(l)
            if t and t > run_start + RUN_WINDOW:
                break
            block.append(l)
        sent = next((m for l in block if (m := LIST_SENT.search(l))), None)
        if sent:
            # The log says sent; the send log says whether Telegram took it.
            delivered = any(
                r.get("erfolg") is True and SENT_MARK in str(r.get("text_anfang") or "")
                and (t := _parse_at(r.get("ts"))) and run_start <= t <= run_start + RUN_WINDOW
                for r in _jsonl(sent_path))
            listing = {"ok": delivered,
                       "text": f"Leseliste {sent.group(1)} von {sent.group(2)} "
                               + ("gesendet" if delivered else "laut Log gesendet, nicht im Sendeprotokoll")}
        elif any(NO_CANDIDATES.search(l) for l in block):
            listing = {"ok": True, "text": "keine Kandidaten, keine Liste"}
        elif any(PAUSED.search(l) for l in block):
            listing = {"ok": True, "text": "Lesen pausiert (Breaker), keine Liste"}
        else:
            listing = {"ok": False, "text": "Lauf ohne Leseliste und ohne erklärten Grund"}

    # 2. Drafter: model calls or drafts in the log since `since`, plus the
    # state's per-day draft counter for days from `since`.
    drafter_lines = 0
    for l in log_lines:
        t = _stamp(l)
        if t and t >= since and DRAFTER.search(l):
            drafter_lines += 1
    try:
        state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise Unreadable(f"{state_path}: {exc}") from exc
    first = since.date().isoformat()
    counted = sum(int(v or 0) for k, v in (state.get("per_day") or {}).items() if k >= first)
    drafter = {"ok": drafter_lines == 0 and counted == 0,
               "text": f"Drafter {drafter_lines} Modellaufrufe/Entwürfe, Zähler {counted}"}

    # X writes by the reply branch since `since`.
    replies = sum(1 for r in _jsonl(posts_path)
                  if r.get("kind") == "reply" and (t := _parse_at(r.get("at"))) and t >= since)
    meter = _jsonl(meter_path)
    meter_writes = sum(1 for r in meter
                       if r.get("kind") == "write" and r.get("source") == "reply"
                       and (t := _parse_at(r.get("at"))) and t >= since)
    posted = sum(int(v or 0) for k, v in (state.get("posted_per_day") or {}).items() if k >= first)
    xwrite = {"ok": replies == 0 and meter_writes == 0 and posted == 0,
              "text": f"X-Antworten {max(replies, meter_writes, posted)}"}

    # Schedule: list and mentions only, no 10:05/16:05 run that day, page 40.
    end = run_start + RUN_WINDOW
    # A paused run returns before it logs its window, and reads nothing.
    no_search = any(WINDOW_NO_SEARCH.search(l) or PAUSED.search(l) for l in block)
    extra_runs = [m.group(1)[-5:] for l in log_lines
                  if (m := RUN_START.search(l)) and m.group(1)[:10] == day.isoformat()
                  and m.group(1)[-5:] in RETIRED_RUNS]
    pages = [len(r.get("posts") or []) for r in meter
             if r.get("kind") == "read" and r.get("source") == LIST_SOURCE
             and (t := _parse_at(r.get("at"))) and run_start <= t <= end]
    page = max(pages, default=0)
    problems = (["kein 06:05-Lauf"] if start_idx is None else
                [] if no_search else ["mit Suche"]) \
        + [f"Lauf {x}" for x in extra_runs] \
        + ([f"Listenseite {page} > {LIST_PAGE_MAX}"] if page > LIST_PAGE_MAX else [])
    schedule = {"ok": not problems,
                "text": "Takt " + (", ".join(problems) if problems else
                                   f"ok (ohne Suche, nur 06:05, Seite ≤ {LIST_PAGE_MAX})")}

    # 4. Cost of the run's reads: resources inside the window, each once,
    # priced like the meter. Other readers in the same ten minutes would be
    # counted too, so this is an upper bound for the run.
    posts, users = set(), set()
    for r in meter:
        if r.get("kind") != "read":
            continue
        t = _parse_at(r.get("at"))
        if t and run_start <= t <= end:
            posts.update(r.get("posts") or [])
            users.update(r.get("users") or [])
    usd = round(len(posts) * USD_PER_POST_READ + len(users) * USD_PER_USER_READ, 3)
    cost = {"ok": usd <= COST_CAP_USD,
            "text": f"Kosten ${usd:.3f} ({len(posts)} Posts, {len(users)} Profile; Grenze ${COST_CAP_USD:.2f})"}

    checks = {"leseliste": listing, "takt": schedule, "drafter": drafter,
              "x_write": xwrite, "kosten": cost}
    return {"day": day, "checks": checks,
            "failed": [k for k, v in checks.items() if not v["ok"]]}


def _sources() -> dict:
    return {"log_path": paths.logs("reply_radar.log"),
            "state_path": paths.data("reply_radar_state.json"),
            "posts_path": paths.data("x_posts.jsonl"),
            "meter_path": paths.data("x_meter.jsonl"),
            "sent_path": os.path.expanduser("~/selftest/telegram-sent.jsonl")}


def run(now: dt.datetime | None = None, **sources) -> dict | None:
    """None before the first due run; otherwise evaluate()'s result."""
    now = now or dt.datetime.now(dt.timezone.utc)
    start = retired_from()
    day = due_day(now, start)
    if day is None:
        return None
    since = dt.datetime.combine(start, dt.time(0, 0), dt.timezone.utc)
    return evaluate(day, since, **(sources or _sources()))


def lines(now: dt.datetime | None = None, **sources) -> list[str]:
    """The Sammelmeldung's line. Always exactly one."""
    try:
        r = run(now, **sources)
    except Unreadable as exc:
        return [f"Reply-Radar (BY5): nicht lesbar — {exc}"]
    if r is None:
        return ["Reply-Radar (BY5): erste Prüfung nach dem Lauf am 18.10. 06:05 UTC"]
    verdict = "OK" if not r["failed"] else "ABWEICHUNG " + ", ".join(r["failed"])
    parts = " · ".join(c["text"] for c in r["checks"].values())
    return [f"Reply-Radar (BY5) {r['day']:%d.%m.} 06:05: {verdict} — {parts}"]


def main() -> int:
    try:
        r = run()
    except Unreadable as exc:
        print(f"UNREADABLE: {exc}")
        return 2
    if r is None:
        print("not due before 18.10.2026 06:23 UTC")
        print(0)
        return 0
    for name, c in r["checks"].items():
        print(f"{'ok  ' if c['ok'] else 'FAIL'} {name}: {c['text']}")
    print(len(r["failed"]))
    return 1 if r["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
