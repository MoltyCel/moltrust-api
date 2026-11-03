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

from app import paths

log = logging.getLogger("x_meter")

# None means "ask app.paths", which reads MOLTRUST_ROOT every time. A path
# assigned here wins — that is how a test redirects the ledger without an
# environment variable. What must not come back is a module constant computed
# at import: nothing downstream can redirect that, and this is the file the
# breaker decides on.
LEDGER: str | None = None


def ledger_path() -> str:
    return LEDGER or paths.data("x_meter.jsonl")

USD_PER_POST_READ = 0.005
USD_PER_USER_READ = 0.010
USD_PER_POST_WRITE = 0.015
USD_PER_POST_WRITE_WITH_URL = 0.200

# CM8: X makes a link out of more than an http:// prefix — a bare domain in the
# text ("moltrust.ch", "github.com/x") is linkified in the timeline just the
# same. One pattern for both jobs: the send guard refuses what this finds, and
# the ledger books what this finds at the link price, so the two can never
# drift apart. Whether X bills a bare domain at the link price is not
# documented (c); the post carries a link either way, and booking the higher
# price is the side that cannot understate the week.
_LINK_TLDS = ("com|ch|io|net|org|dev|ai|app|xyz|co|de|eu|me|so|sh|gg|to|"
              "info|tech|cloud|link|news")
URL_RE = re.compile(
    r"https?://"
    r"|\bt\.co/"
    r"|\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:" + _LINK_TLDS + r")\b(?![a-z0-9-])",
    re.I)


def link_in(text: str) -> str:
    """The first thing in `text` that X can turn into a link, or "".

    Returned instead of a bool because the refusal names what it found: a
    guard that only says "there is a link in here" sends the person back to
    read 280 characters looking for it.
    """
    m = URL_RE.search(text or "")
    return m.group(0) if m else ""


def _day(now: datetime.datetime | None = None) -> str:
    return (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%d")


def _write(row: dict) -> None:
    """Best-effort. A meter that fails must never cost a call that worked."""
    try:
        led = ledger_path()
        os.makedirs(os.path.dirname(led), exist_ok=True)
        with open(led, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        os.chmod(led, 0o640)
    except Exception as e:
        log.warning(f"meter write failed: {type(e).__name__}: {e}")


# CM9 (Lars 22.10.2026): the radar's reading and sending, and the CM5
# measurement, each in a column of their own. A source name alone cannot carry
# that — the radar's reads were booked under the URL tail ("tweets", "recent",
# "mentions", a post id), names other readers can produce too, and scripts
# such as check_radar_reading_list.py key on them. So the row keeps its source
# and gains `origin`: "radar" on every read the radar makes, and on every
# write booked as a radar reply (REPLY_SEND_SOURCES below). The measurement is
# recognised by its own source, which it already has.
ORIGIN_RADAR = "radar"
CM5_MEASUREMENT_SOURCE = "lars-reply-metrics"    # scripts/lars_reply_metrics.py
# CM22: the reads on @LarsOnMoltrust that are not the daily measurement — the
# timeline read that matches his replies to drafts, and the one users/me check
# at the grant. Reads, booked in the CM5 column, never in the send cap (CM4).
LARS_MATCH_SOURCE = "lars-reply-match"            # agents/lars_reply_match.py
LARS_GRANT_SOURCE = "lars-oauth2-grant"           # app/x_lars_oauth2.py
CM5_READ_SOURCES = (CM5_MEASUREMENT_SOURCE, LARS_MATCH_SOURCE, LARS_GRANT_SOURCE)


def record_read(body: dict, source: str = "", users_in_data: bool = False,
                origin: str = "") -> None:
    """Book the resources one response returned.

    Takes the parsed body so the caller does not have to know which fields X
    bills for: `data` is posts, `includes.users` is profiles. On a user lookup
    (`users_in_data`) `data` is profiles too, and a profile costs twice a post.
    """
    if not isinstance(body, dict):
        return
    data = body.get("data")
    ids = [d.get("id") for d in data] if isinstance(data, list) else (
        [data.get("id")] if isinstance(data, dict) else [])
    posts = [] if users_in_data else ids
    users = [u.get("id") for u in (body.get("includes", {}) or {}).get("users", [])]
    if users_in_data:
        users = ids + users
    posts = [p for p in posts if p]
    users = [u for u in users if u]
    if not posts and not users:
        return
    row = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "kind": "read", "source": source, "posts": posts, "users": users}
    if origin:
        row["origin"] = origin
    _write(row)


def record_write(tweet_id: str, text: str, source: str = "") -> None:
    row = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "kind": "write", "source": source, "id": tweet_id,
           "with_url": bool(URL_RE.search(text or ""))}
    # A radar reply is the one write the radar makes (CM9); its name in the
    # ledger is fixed by REPLY_SEND_SOURCES, the same list the send cap reads.
    if source in REPLY_SEND_SOURCES:
        row["origin"] = ORIGIN_RADAR
    _write(row)


def record_correction(usd: float, reason: str, day: str | None = None) -> None:
    """Book an amount the meter got wrong, as its own line.

    The ledger is append-only: a misbooked read stays as it was booked, and the
    difference stands next to it with its reason. Added 11.10.2026 (BP5) for
    two list-member reads booked as posts instead of profiles.
    """
    _write({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "kind": "correction", "source": "correction", "usd": round(float(usd), 3),
            "day": day or _day(), "reason": reason})


def spend(day: str | None = None, ledger: str | None = None) -> dict:
    """What that day cost, with the per-UTC-day deduplication X applies.

    `ledger` resolves at call time, not at import. A default of `LEDGER` is
    frozen when the module loads, so reassigning x_meter.LEDGER afterwards
    changes nothing and every caller keeps reading the old path — which is the
    silent-wrong-answer shape this meter exists to avoid.
    """
    day = day or _day()
    ledger = ledger or ledger_path()
    posts: set[str] = set()
    users: set[str] = set()
    writes_plain = writes_url = 0
    corrections = 0.0
    by_source: dict[str, set] = {}
    try:
        with open(ledger) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("kind") == "correction":
                    if row.get("day") == day:
                        corrections += float(row.get("usd") or 0)
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
        return {"day": day, "usd": 0.0, "corrections": 0.0, "posts": 0, "users": 0,
                "writes": 0, "by_source": {}, "ledger": False}
    usd = (len(posts) * USD_PER_POST_READ + len(users) * USD_PER_USER_READ
           + writes_plain * USD_PER_POST_WRITE
           + writes_url * USD_PER_POST_WRITE_WITH_URL + corrections)
    return {"day": day, "usd": round(usd, 3), "corrections": round(corrections, 3),
            "posts": len(posts),
            "users": len(users), "writes": writes_plain + writes_url,
            "writes_with_url": writes_url,
            "by_source": {k: len(v) for k, v in sorted(
                by_source.items(), key=lambda kv: -len(kv[1]))},
            "ledger": True}


# The budget, raised 2026-10-04 after a week of measurement. The first version
# was set on 2026-09-27 against a day that itself cost $2.555, so the breaker
# stood below the operating cost from the hour it was written: seven of seven
# days over it, none under the target, $1.883 a day on average, $56.49 a month
# against a $25 band.
#
#   $60 a month, and the breaker at $2.00 a day — both as instructed on
#   2026-10-04.
#
# Those two numbers meet: $60 over 30 days is $2.00, so the breaker sits on
# the average rather than above it. The ladder target → alarm → stop needs room
# between its rungs, so the target is the measured mean, $1.90, and the alarm
# sits just under the breaker. Said plainly: against last week's spread
# ($1.61 to $2.56, mean $1.883) a $2.00 breaker still stops reading on the
# heavier half of days — two of seven would have tripped it.
#
# And a second limit, because the daily dollar alone does not say where the
# money goes: profile reads cost twice a post read and the day's 66 of them are
# $0.66 of a $1.66 day. 33 a day is half of that, and it is a cap on the
# expansions a search asks for, not on the search.
#
# The gap between target and alarm is deliberate: a heavy day is allowed to
# happen and be seen, a runaway day is not allowed to finish.
MONTHLY_TARGET_USD = 60.0
DAILY_TARGET_USD = 1.90
DAILY_ALARM_USD = 1.95
# Distinct profiles a UTC day may fetch. See the budget note above.
DAILY_PROFILE_READS = 33

# Above this, reading stops for the rest of the UTC day. The alarm tells you;
# the breaker makes it stop. A day that has already cost $1.50 will not be
# argued back down by a report nobody reads until Sunday.
DAILY_BREAK_USD = 2.00

# Posting is never broken. A digest costs $0.015 and is the thing the account
# exists for; reading is what runs away with the money.
BREAKER_FLAG: str | None = None

# The two numbers the reply radar runs on, at one place (Lars, CM4,
# 2026-10-22): how many drafts a UTC day may carry, and what sending replies
# may cost in a week.
#
# The cap counts sending only. Reading never counts against it, and neither
# does the measurement from CM5 (Lars, CM7) — the reading list alone cost
# $0.78 in the week of 19.10 against a $0.50 cap, so a cap that counted both
# would stand below the operating cost from the hour it was written, which is
# the mistake DAILY_BREAK_USD above already made once.
#
# When it trips, sending pauses and nothing else (Lars, CM10): the reading
# list, the drafts and the measurement all keep running.
WEEKLY_SEND_CAP_USD = 0.50
DAILY_DRAFT_CAP = 2

# What a radar reply is called in the ledger. x_post.post passes `kind` as the
# ledger source, and the radar posts with kind="reply" — digest, proof and
# syndication carry their own names and are out of scope for this cap.
REPLY_SEND_SOURCES = ("reply",)


def flag_path() -> str:
    return BREAKER_FLAG or paths.data("x_reads_paused")


def trip_breaker(day: str, usd: float) -> bool:
    """Write the flag. True when this run is the one that tripped it."""
    try:
        if os.path.exists(flag_path()):
            with open(flag_path()) as f:
                if json.load(f).get("day") == day:
                    return False
        os.makedirs(os.path.dirname(flag_path()), exist_ok=True)
        with open(flag_path(), "w") as f:
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
        with open(flag_path()) as f:
            flag = json.load(f)
    except Exception:
        return None
    if flag.get("day") != day:
        return None
    return (f"X reads paused: ${float(flag.get('usd', 0)):.2f} spent today "
            f"per the watchdog flag (limit ${DAILY_BREAK_USD:.2f}) — "
            f"paused until 00:00 UTC")


def profiles_exhausted(now: datetime.datetime | None = None) -> str | None:
    """Why no further profile should be fetched today, or None.

    Separate from reads_paused on purpose: when the profile budget is spent the
    posts still matter, so the caller drops the user expansion and keeps
    reading. A single switch would have stopped both.

    Counted over distinct profile ids, the same deduplication X bills by, so
    re-seeing an author costs nothing and does not count against the cap.
    """
    s = spend(_day(now))
    if s["users"] < DAILY_PROFILE_READS:
        return None
    return (f"profile reads spent: {s['users']} distinct profiles today "
            f"(cap {DAILY_PROFILE_READS}) — expansions dropped until 00:00 UTC")


def week_window(now: datetime.datetime | None = None) -> tuple[str, str]:
    """The UTC week the cap counts over: Monday 00:00 to Sunday 23:59 (CM4).

    Returned as the first and last UTC day, because the ledger stamps days and
    the cap is read against whole days. Monday itself is the first day of its
    own week, which is the boundary a `weekday() - 1` gets wrong.
    """
    today = (now or datetime.datetime.now(datetime.timezone.utc)).date()
    monday = today - datetime.timedelta(days=today.weekday())
    return monday.isoformat(), (monday + datetime.timedelta(days=6)).isoformat()


def next_monday(now: datetime.datetime | None = None) -> str:
    """The day the pause lifts: the Monday after this week's (CM4)."""
    monday, _ = week_window(now)
    return (datetime.date.fromisoformat(monday)
            + datetime.timedelta(days=7)).isoformat()


def send_spend(now: datetime.datetime | None = None,
               ledger: str | None = None) -> dict:
    """What sending replies has cost in this UTC week (CM4).

    Only `write` rows whose source is a reply count. Reads are not read at all
    here — not filtered out afterwards, never summed in the first place, so a
    reading list that costs more than the cap cannot move this number.

    A correction row carries no source and is therefore not a send. Corrections
    exist for read volume the meter missed; attributing one to the send cap
    would let a read mistake close the send path.
    """
    first, last = week_window(now)
    ledger = ledger or ledger_path()
    plain = url = 0
    try:
        with open(ledger) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("kind") != "write":
                    continue
                if (row.get("source") or "") not in REPLY_SEND_SOURCES:
                    continue
                day = (row.get("at") or "")[:10]
                if day < first or day > last:
                    continue
                if row.get("with_url"):
                    url += 1
                else:
                    plain += 1
    except FileNotFoundError:
        return {"week": (first, last), "usd": 0.0, "sends": 0, "ledger": False}
    usd = plain * USD_PER_POST_WRITE + url * USD_PER_POST_WRITE_WITH_URL
    return {"week": (first, last), "usd": round(usd, 3), "sends": plain + url,
            "with_url": url, "ledger": True}


RADAR_COLUMNS = ("radar_read", "radar_send", "cm5_read")


def _column(row: dict) -> str | None:
    """Which CM9 column a ledger row belongs to, or None."""
    kind = row.get("kind")
    if kind == "read":
        if row.get("origin") == ORIGIN_RADAR:
            return "radar_read"
        if row.get("source") in CM5_READ_SOURCES:
            return "cm5_read"
    elif kind == "write" and (row.get("origin") == ORIGIN_RADAR
                              or (row.get("source") or "") in REPLY_SEND_SOURCES):
        return "radar_send"
    return None


def radar_split(day: str, ledger: str | None = None) -> dict:
    """One UTC day in the three CM9 columns, in dollars.

    Each column is deduplicated within itself, the way X bills a day: a post
    the radar read twice is one read. Across columns there is no netting — a
    post both the radar and the measurement read stands in both, because each
    column answers "what does this reader cost", which is what a cap on it
    needs. Rows with no column (digest, Herald, probes) are left out; the
    day's bill is spend().
    """
    ledger = ledger or ledger_path()
    posts = {c: set() for c in RADAR_COLUMNS}
    users = {c: set() for c in RADAR_COLUMNS}
    plain = url = 0
    found = False
    try:
        with open(ledger) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (row.get("at") or "")[:10] != day:
                    continue
                found = True
                col = _column(row)
                if col == "radar_send":
                    if row.get("with_url"):
                        url += 1
                    else:
                        plain += 1
                elif col:
                    posts[col].update(row.get("posts") or [])
                    users[col].update(row.get("users") or [])
    except FileNotFoundError:
        pass
    out = {"day": day, "ledger": found}
    for col in ("radar_read", "cm5_read"):
        out[col] = round(len(posts[col]) * USD_PER_POST_READ
                         + len(users[col]) * USD_PER_USER_READ, 3)
        out[col + "_posts"] = len(posts[col])
        out[col + "_users"] = len(users[col])
    out["radar_send"] = round(plain * USD_PER_POST_WRITE
                              + url * USD_PER_POST_WRITE_WITH_URL, 3)
    out["radar_sends"] = plain + url
    return out


def sends_paused(now: datetime.datetime | None = None,
                 ledger: str | None = None) -> str | None:
    """Why sending a reply is paused, or None (CM4).

    Sending only. Nothing here is consulted by the reading list, the drafter or
    the measurement — a cost pause must not cut the CM5 measurement short, and
    a single switch for both is how reads_paused above ended up stopping more
    than it meant to.
    """
    s = send_spend(now, ledger)
    if s["usd"] < WEEKLY_SEND_CAP_USD:
        return None
    return (f"Senden pausiert: ${s['usd']:.2f} für {s['sends']} Antworten in "
            f"der Woche ab {s['week'][0]} (Deckel "
            f"${WEEKLY_SEND_CAP_USD:.2f}) — wieder frei am "
            f"{next_monday(now)} um 00:00 UTC")


def check(day: str | None = None, ledger: str | None = None) -> dict:
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


# ── The one way to read X ──
#
# Every GET against the X API goes through get(), so every read lands in the
# ledger. On 11.10.2026 the meter was found to miss reads it was supposed to
# see: the hourly user lookup in supervision.py, the follower reads in
# digest_metrics.py, x_value_log.py, sm_kpis.py and reply_radar.py, the x_budget
# probe, and about $0.16 of hand reads by a diagnosis session. A meter that
# misses its own system's reads measures part of the bill and calls it the
# bill. tests/test_x_reads_metered.py fails on any direct GET to an X host
# outside this module.

X_HOSTS = ("api.twitter.com", "api.x.com")

# /2/users/me, /2/users/<id>, /2/users/by/username/<name>, /2/users/by return
# profiles in `data`; /2/users/<id>/tweets and /mentions return posts. So do
# the user lists: list members, followers, following, liking users, reposters.
# On 11.10.2026 two reads of the 74 list members were booked as 145 posts.
_USER_LOOKUP_RE = re.compile(
    r"/2/(?:users/(?:me|\d+|by/username/[^/?]+|by)"
    r"|lists/\d+/members"
    r"|users/\d+/(?:followers|following)"
    r"|tweets/\d+/(?:liking_users|retweeted_by))/?(?:\?|$)")


def is_user_lookup(url: str) -> bool:
    return bool(_USER_LOOKUP_RE.search(url))


def get(url: str, params: dict | None = None, auth=None, source: str = "",
        timeout: float = 30, origin: str = "", **kwargs):
    """requests.get against X, with the response booked in the ledger.

    Returns the requests.Response unchanged; errors raise as they would from
    requests.get. Only a 200 is booked: X does not bill a refused read. It does
    not consult the breaker — whether a read may happen is the caller's call,
    and supervision has to be able to look while the radar is paused.
    """
    import requests
    r = requests.get(url, params=params, auth=auth, timeout=timeout, **kwargs)
    if getattr(r, "status_code", None) == 200:
        # The meter must never cost a call that worked (see _write).
        try:
            body = r.json()
        except Exception:
            body = None
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        record_read(body, source=source or path.split("?")[0].rsplit("/", 1)[-1],
                    users_in_data=is_user_lookup(url), origin=origin)
    return r


def _cli(argv: list[str]) -> int:
    """python -m agents.x_meter get <url> [key=value ...] [--source NAME]

    For reads by hand or by a session. Signs with OAuth 1.0a from the X_*
    variables in the environment, prints the JSON body, books the read.
    """
    if len(argv) < 2 or argv[0] != "get":
        print(_cli.__doc__)
        return 2
    from requests_oauthlib import OAuth1
    keys = [os.getenv(k, "") for k in
            ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    if not all(keys):
        print("X_* credentials missing from the environment")
        return 2
    url, rest = argv[1], argv[2:]
    source = "manual"
    if "--source" in rest:
        i = rest.index("--source")
        source = "manual:" + rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    params = dict(a.split("=", 1) for a in rest)
    r = get(url, params=params, auth=OAuth1(*keys), source=source)
    print(r.text)
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
