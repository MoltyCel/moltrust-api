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
import time

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


# The budget, set 2026-09-27 after measuring what a run actually costs.
#
#   $25 a month, which is $0.80 on an average day
#   report above $1.00 — a day that would be $30 a month
#   stop reading at $1.50 — a day that would be $45
#
# The gap between target and alarm is deliberate: a heavy day is allowed to
# happen and be seen, a runaway day is not allowed to finish.
MONTHLY_TARGET_USD = 25.0
DAILY_TARGET_USD = 0.80
DAILY_ALARM_USD = 1.0

# Above this, reading stops for the rest of the UTC day. The alarm tells you;
# the breaker makes it stop. A day that has already cost $1.50 will not be
# argued back down by a report nobody reads until Sunday.
DAILY_BREAK_USD = 1.50

# Posting is never broken. A digest costs $0.015 and is the thing the account
# exists for; reading is what runs away with the money.
BREAKER_FLAG = os.path.join(os.path.expanduser("~/moltstack/data"), "x_reads_paused")


def trip_breaker(day: str, usd: float) -> bool:
    """Write the flag. True when this run is the one that tripped it."""
    try:
        if os.path.exists(BREAKER_FLAG):
            with open(BREAKER_FLAG) as f:
                if json.load(f).get("day") == day:
                    return False
        os.makedirs(os.path.dirname(BREAKER_FLAG), exist_ok=True)
        with open(BREAKER_FLAG, "w") as f:
            json.dump({"day": day, "usd": usd,
                       "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, f)
        return True
    except Exception as e:
        log.error(f"could not write the breaker flag: {type(e).__name__}: {e}")
        return False


# The live sum is recomputed at most this often. One radar run calls _get a
# dozen times and the ledger does not change between them; re-reading the file
# each time buys nothing.
_LIVE_CACHE_S = 20
_live: dict = {"at": 0.0, "day": "", "usd": 0.0}


def live_spend_usd(now: datetime.datetime | None = None) -> float:
    """Today's spend from the ledger, briefly cached."""
    day = _day(now)
    if _live["day"] == day and time.monotonic() - _live["at"] < _LIVE_CACHE_S:
        return _live["usd"]
    usd = spend(day)["usd"]
    _live.update({"at": time.monotonic(), "day": day, "usd": usd})
    return usd


def reads_paused(now: datetime.datetime | None = None) -> str | None:
    """Why reading is paused, or None. Computed, not looked up.

    The flag was the only source until 02.10.2026, and the watchdog writes it
    hourly — so a day could run up to an hour past the breaker with reads still
    going. It did: the day stood at $1.54 against a $1.50 limit and this
    function answered "open", because the flag still said 01.10.

    The flag stays as a cache and as the watchdog's record of when it tripped,
    but **the live sum decides**. A day's spend never falls, so the two
    disagree in one direction only: the flag lagging behind.

    Posting is not affected. agents/x_post.py does not consult this.
    """
    day = _day(now)
    usd = live_spend_usd(now)
    if usd >= DAILY_BREAK_USD:
        return (f"X reads paused: ${usd:.2f} spent today "
                f"(limit ${DAILY_BREAK_USD:.2f}) — paused until 00:00 UTC")
    # The cache, for the case where the ledger is unreadable and the watchdog
    # had already decided: a breaker that fails open on a missing file is not a
    # breaker.
    try:
        with open(BREAKER_FLAG) as f:
            flag = json.load(f)
    except Exception:
        return None
    if flag.get("day") != day:
        return None
    return (f"X reads paused: ${float(flag.get('usd', 0)):.2f} spent today "
            f"per the watchdog flag (limit ${DAILY_BREAK_USD:.2f}) — "
            f"paused until 00:00 UTC")


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
    if s["usd"] >= DAILY_BREAK_USD:
        detail += f" · über dem Breaker (${DAILY_BREAK_USD:.2f})"
    elif s["usd"] > DAILY_ALARM_USD:
        detail += f" · Soll ${DAILY_TARGET_USD:.2f}/Tag"
    return {"ok": s["usd"] <= DAILY_ALARM_USD, "surface": "XSpend",
            "detail": detail, "spend": s,
            "break": s["usd"] >= DAILY_BREAK_USD}
