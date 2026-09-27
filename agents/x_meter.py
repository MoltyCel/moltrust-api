"""What the X API costs us today, counted rather than estimated.

X bills pay-per-use, and the unit is not the request (docs.x.com, pricing,
read 2026-09-27):

    post read          $0.005 per resource returned
    user/profile read  $0.010 per resource returned
    post created       $0.015 per request
    post with a URL    $0.200 per request

Two consequences that decide how this is built.

**Reads are charged per object, not per call.** A list page of 100 posts costs
a hundred reads, not one. That is how twelve runs a day emptied the account:
12 × (100 list + 125 search + 25 mentions) is 3000 objects, $15 a day.

**Resources are deduplicated within a UTC day.** Reading the same post twice
today is charged once. So the honest figure is the count of *distinct* ids seen
today, which means the meter has to remember ids, not add up numbers. A meter
that summed response sizes would overstate the bill by a wide margin — the
radar re-reads much the same list every run.

A write with a URL costs forty times a plain one, which is worth knowing before
choosing where the link goes.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re

log = logging.getLogger("x_meter")

LEDGER = os.path.join(os.path.expanduser("~/moltstack/data"), "x_meter.jsonl")

USD_PER_POST_READ = 0.005
USD_PER_USER_READ = 0.010
USD_PER_POST_WRITE = 0.015
USD_PER_POST_WRITE_WITH_URL = 0.200

URL_RE = re.compile(r"https?://|\bt\.co/")


def _day(now: datetime.datetime | None = None) -> str:
    return (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%d")


def _write(row: dict) -> None:
    """Best-effort. A meter that fails must never cost a call that worked."""
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        os.chmod(LEDGER, 0o640)
    except Exception as e:
        log.warning(f"meter write failed: {type(e).__name__}: {e}")


def record_read(body: dict, source: str = "") -> None:
    """Book the resources one response returned.

    Takes the parsed body so the caller does not have to know which fields X
    bills for: `data` is posts, `includes.users` is profiles.
    """
    if not isinstance(body, dict):
        return
    data = body.get("data")
    posts = [d.get("id") for d in data] if isinstance(data, list) else (
        [data.get("id")] if isinstance(data, dict) else [])
    users = [u.get("id") for u in (body.get("includes", {}) or {}).get("users", [])]
    posts = [p for p in posts if p]
    users = [u for u in users if u]
    if not posts and not users:
        return
    _write({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "kind": "read", "source": source, "posts": posts, "users": users})


def record_write(tweet_id: str, text: str, source: str = "") -> None:
    _write({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "kind": "write", "source": source, "id": tweet_id,
            "with_url": bool(URL_RE.search(text or ""))})


def spend(day: str | None = None, ledger: str = LEDGER) -> dict:
    """What that day cost, with the per-UTC-day deduplication X applies."""
    day = day or _day()
    posts: set[str] = set()
    users: set[str] = set()
    writes_plain = writes_url = 0
    by_source: dict[str, set] = {}
    try:
        with open(ledger) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (row.get("at") or "")[:10] != day:
                    continue
                if row.get("kind") == "write":
                    if row.get("with_url"):
                        writes_url += 1
                    else:
                        writes_plain += 1
                    continue
                seen = by_source.setdefault(row.get("source") or "?", set())
                for p in row.get("posts") or []:
                    posts.add(p)
                    seen.add(p)
                for u in row.get("users") or []:
                    users.add(u)
                    seen.add(u)
    except FileNotFoundError:
        return {"day": day, "usd": 0.0, "posts": 0, "users": 0,
                "writes": 0, "by_source": {}, "ledger": False}
    usd = (len(posts) * USD_PER_POST_READ + len(users) * USD_PER_USER_READ
           + writes_plain * USD_PER_POST_WRITE
           + writes_url * USD_PER_POST_WRITE_WITH_URL)
    return {"day": day, "usd": round(usd, 3), "posts": len(posts),
            "users": len(users), "writes": writes_plain + writes_url,
            "writes_with_url": writes_url,
            "by_source": {k: len(v) for k, v in sorted(
                by_source.items(), key=lambda kv: -len(kv[1]))},
            "ledger": True}


# Above this the day is reported. $1 a day is $30 a month against a $10 target,
# so the alarm is set where the month is already lost, not where it is at risk.
DAILY_ALARM_USD = 1.0


def check(day: str | None = None, ledger: str = LEDGER) -> dict:
    s = spend(day, ledger)
    if not s["ledger"]:
        return {"ok": True, "surface": "XSpend",
                "detail": "no meter rows yet today", "spend": s}
    detail = (f"${s['usd']:.2f} today — {s['posts']} posts, {s['users']} profiles, "
              f"{s['writes']} writes")
    if s["by_source"]:
        top = list(s["by_source"].items())[:3]
        detail += " · " + ", ".join(f"{k} {n}" for k, n in top)
    return {"ok": s["usd"] <= DAILY_ALARM_USD, "surface": "XSpend",
            "detail": detail, "spend": s}
