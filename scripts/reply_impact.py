"""Did the replies do anything. The figure the 18.10 decision rests on.

The reply branch is on probation: fourteen days from 2026-10-04, and without
measurable effect it is discontinued. This produces the number, and — more
important — says which part of it can carry a decision and which cannot.

**What each column is worth.**

    impressions     how many people saw it. Reliable, per reply.
    profile_clicks  how many went from the reply to our profile. The one
                    column that measures the reply doing something *for us*
                    rather than for somebody else's thread. Reliable, per reply.
    likes           weak at this scale: one like is one person.
    followers_24h   **not attributable.** Seven replies and a daily digest share
                    one follower count. A follower gained on a day with three
                    posts belongs to none of them in particular. Reported
                    because it was asked for, never as the deciding figure.

So the decision rule this offers is impressions and profile clicks per reply,
against the same two for the digest series over the same days — the comparison
the video series already uses. A branch that costs $0.22 a draft and returns
no profile click in two weeks has not earned the slot.

Since 2026-10-12 the report also carries, as context and never as an input to
the threshold: replies, digest and syndication hooks side by side on the same
columns; the radar's cost (X reads and model tokens) per day, per reply and
per profile click; the follower count on the first and the last day; and
every rise in the count with the posts of the 24 hours before it.

    python3 scripts/reply_impact.py              # the table
    python3 scripts/reply_impact.py --send       # and to Telegram
    python3 scripts/reply_impact.py --since 2026-10-04
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify
from agents import x_meter

BASE = os.path.expanduser("~/moltstack")
METRICS = os.path.join(BASE, "data", "digest_metrics.jsonl")
DECISION_DATE = "2026-10-18"
WINDOW_START = "2026-10-04"

# Fixed on 2026-10-04, before the window it judges, and recorded in
# docs/reply-radar.md so it cannot drift. Both conditions, not either.
#
# A median rather than a mean: one reply under a 53 000-impression target post
# would carry a mean by itself and say nothing about the other twelve. And
# profile clicks as the second condition, because impressions alone measure
# somebody else's thread — four hundred people scrolling past is not an effect.
MEDIAN_IMPRESSIONS_MIN = 30
CLICKS_PER_REPLY_MIN = 0.5


def verdict(k: dict) -> dict:
    """Continue or discontinue, computed from the thresholds, not from a mood."""
    w = k["window"]
    n = w["impressions"]["n"]
    median = w["impressions"]["median"]
    clicks = w["profile_clicks"]["sum"]
    ratio = (clicks / n) if n else None
    met_median = median is not None and median >= MEDIAN_IMPRESSIONS_MIN
    met_clicks = ratio is not None and ratio >= CLICKS_PER_REPLY_MIN
    return {
        "replies": n, "median_impressions": median,
        "clicks": clicks, "clicks_per_reply": round(ratio, 2) if ratio else ratio,
        "median_ok": met_median, "clicks_ok": met_clicks,
        # No replies at all is not a pass. A branch that produced nothing in
        # fourteen days has answered the question it was asked.
        "continue": bool(n and met_median and met_clicks),
    }


RADAR_STATE = os.path.join(BASE, "data", "reply_radar_state.json")
FOLLOWERS = os.path.join(BASE, "data", "followers.jsonl")
POSTS = os.path.join(BASE, "data", "x_posts.jsonl")
X_LEDGER = os.path.join(BASE, "data", "x_meter.jsonl")
RADAR_LOG = os.path.join(BASE, "logs", "reply_radar.log")


def by_prompt(days: int = 14) -> dict:
    """Candidates, drafts and gate-passes per day per prompt version.

    Separated because the drafter changed on 2026-10-05 and the 18.10 decision
    has to be read against the version that was running. A rate averaged over
    two prompts describes neither.
    """
    try:
        with open(RADAR_STATE) as f:
            book = (json.load(f).get("by_prompt") or {})
    except Exception:
        return {}
    cut = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    return {d: v for d, v in book.items() if d >= cut}


def rows(kind: str) -> list[dict]:
    out = []
    try:
        with open(METRICS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("kind") == kind:
                    out.append(r)
    except OSError:
        return []
    return out


def latest_per_post(kind: str, since: str | None = None) -> list[dict]:
    """The newest measurement of each post — the one with the final numbers."""
    best: dict[str, dict] = {}
    for r in rows(kind):
        tid = r.get("tweet_id")
        if not tid:
            continue
        if since and (r.get("posted_at") or "") < since:
            continue
        cur = best.get(tid)
        if cur is None or (r.get("measured_at") or "") > (cur.get("measured_at") or ""):
            best[tid] = r
    return sorted(best.values(), key=lambda r: r.get("posted_at") or "")


# Enforced on read, not only on write. The first version of the writer allowed
# up to 48 h and stamped one delta at 47.3 h; the row stays as the record of
# what was measured, and this refuses to report it as a 24-hour figure. Same
# shape as the claim guard, which withheld claims for four days because its
# window was only applied when writing.
READ_WINDOW = (12.0, 36.0)


def delta_24h(tid: str) -> tuple[int | None, float | None]:
    """The 24 h follower delta if a measurement fell in the window, else None.

    Deliberately not the nearest available number. A delta taken at 80 hours is
    not a 24-hour delta, and filling the column from it would make four
    unmeasured replies look measured.
    """
    best = None
    for r in rows("reply"):
        if r.get("tweet_id") != tid or r.get("followers_delta_24h") is None:
            continue
        age = r.get("delta_24h_taken_at_hours")
        if age is None or not READ_WINDOW[0] <= age <= READ_WINDOW[1]:
            continue
        # Closest to 24 h wins, so a second measurement inside the window does
        # not overwrite a better one by arriving later.
        if best is None or abs(age - 24) < abs(best[1] - 24):
            best = (r["followers_delta_24h"], age)
    return best if best else (None, None)


# ── Costs ──
#
# What the branch costs, next to what it returned. Two sources, both counted
# rather than estimated: the X ledger (agents/x_meter.py) and the token lines
# the radar writes per model call.

# The radar's own X reads. reply_radar._get books each read under the last
# path segment: list and timeline reads as "tweets", search as "recent",
# mentions as "mentions", a single post by its numeric id. Its follower
# baseline is "followers". "tweets" is also the default for any other
# timeline read; on the live ledger every such row came from the radar.
RADAR_X_SOURCES = {"tweets", "recent", "mentions", "followers"}

# Model prices for agents/reply_radar.MODEL, "claude-opus-5", per million
# tokens: $5 input, $25 output; a cache read is 0.1x input, a 5-minute cache
# write 1.25x. tests/test_reply_impact_basis.py fails if the radar's model
# changes, so this table cannot silently price the wrong model.
RADAR_MODEL = "claude-opus-5"
MODEL_USD_PER_MTOK = {"in": 5.00, "out": 25.00,
                      "cache_read": 0.50, "cache_write": 6.25}
# The radar logs tokens since 2026-10-11 (BO7). Before that its model cost is
# not in any file, and the sum is a floor, not a total.
TOKENS_LOGGED_SINCE = "2026-10-11"
TOKEN_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2})T[^\]]*\] INFO:\s+tokens in=(\w+) out=(\w+) "
    r"cache_read=(\w+) cache_write=(\w+)")


def _days(since: str, until: str) -> list[str]:
    d = datetime.date.fromisoformat(since)
    end = datetime.date.fromisoformat(until)
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def radar_x_usd(day: str) -> float:
    """The radar's X reads and reply writes on one day, deduplicated within the
    day the way X bills them."""
    posts, users = set(), set()
    writes = 0.0
    try:
        with open(X_LEDGER) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (r.get("at") or "")[:10] != day:
                    continue
                src = r.get("source") or ""
                if r.get("kind") == "write":
                    if src == "reply":
                        writes += (x_meter.USD_PER_POST_WRITE_WITH_URL
                                   if r.get("with_url") else x_meter.USD_PER_POST_WRITE)
                    continue
                if r.get("kind") != "read":
                    continue
                if src in RADAR_X_SOURCES or src.isdigit():
                    posts.update(r.get("posts") or [])
                    users.update(r.get("users") or [])
    except OSError:
        return 0.0
    return round(len(posts) * x_meter.USD_PER_POST_READ
                 + len(users) * x_meter.USD_PER_USER_READ + writes, 3)


def model_usd_by_day() -> dict[str, float]:
    p = MODEL_USD_PER_MTOK
    out: dict[str, float] = collections.defaultdict(float)
    try:
        with open(RADAR_LOG, errors="replace") as f:
            for line in f:
                m = TOKEN_RE.match(line)
                if not m:
                    continue
                n = [int(v) if v.isdigit() else 0 for v in m.groups()[1:]]
                out[m.group(1)] += (n[0] * p["in"] + n[1] * p["out"]
                                    + n[2] * p["cache_read"]
                                    + n[3] * p["cache_write"]) / 1e6
    except OSError:
        return {}
    return {d: round(v, 3) for d, v in out.items()}


def costs(since: str, until: str) -> dict:
    model = model_usd_by_day()
    days = []
    for d in _days(since, until):
        days.append({"day": d,
                     "x_total": x_meter.spend(d, ledger=X_LEDGER)["usd"],
                     "x_radar": radar_x_usd(d),
                     "model_radar": (model.get(d, 0.0)
                                     if d >= TOKENS_LOGGED_SINCE else None)})
    radar = sum(r["x_radar"] for r in days) + sum(
        r["model_radar"] or 0 for r in days)
    return {"days": days, "radar_usd": round(radar, 2),
            "x_total_usd": round(sum(r["x_total"] for r in days), 2),
            "model_floor": since < TOKENS_LOGGED_SINCE}


# ── Followers ──
#
# Two series: the daily 18:00 reading in digest_metrics.jsonl, and from
# 2026-10-12 the hourly data/followers.jsonl (scripts/followers_series.py).
# Both are net counts.

def _ts(s: str | None) -> datetime.datetime | None:
    try:
        t = datetime.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def daily_followers() -> dict[str, tuple[datetime.datetime, int]]:
    """Per day, the reading closest to 18:00 UTC, from any row that has one."""
    best: dict[str, tuple[datetime.datetime, int]] = {}
    try:
        with open(METRICS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n = r.get("followers_now")
                if n is None:
                    n = (r.get("x") or {}).get("followers_now")
                at = _ts(r.get("measured_at"))
                if n is None or at is None:
                    continue
                day = at.strftime("%Y-%m-%d")
                target = at.replace(hour=18, minute=0, second=0, microsecond=0)
                cur = best.get(day)
                if cur is None or abs(at - target) < abs(cur[0] - target):
                    best[day] = (at, n)
    except OSError:
        return {}
    return best


def hourly_followers() -> list[dict]:
    out = []
    try:
        with open(FOLLOWERS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                at = _ts(r.get("at"))
                if at and r.get("followers") is not None:
                    out.append({"at": at, "followers": r["followers"],
                                "new": len(r.get("new_ids") or [])})
    except OSError:
        return []
    return sorted(out, key=lambda r: r["at"])


def followers_on(day: str, now: datetime.datetime) -> dict:
    """The count at 18:00 UTC on `day`, or the latest one if that hour has not
    come yet — marked as provisional, with its time.

    On the decision date the 07:30 send runs before the 18:00 row exists. It
    shows what there is and says so; the 18:10 run that day carries the real
    figure.
    """
    cutoff = datetime.datetime.fromisoformat(day + "T18:00:00+00:00")
    daily = daily_followers()
    if now >= cutoff:
        for h in hourly_followers():
            if cutoff <= h["at"] < cutoff + datetime.timedelta(hours=1):
                return {"day": day, "followers": h["followers"],
                        "at": h["at"].isoformat(), "provisional": False}
        if day in daily and daily[day][0] >= cutoff - datetime.timedelta(hours=1):
            at, n = daily[day]
            return {"day": day, "followers": n, "at": at.isoformat(),
                    "provisional": False}
    points = [(at, n) for at, n in daily.values()] + [
        (h["at"], h["followers"]) for h in hourly_followers()]
    points = [p for p in points if p[0] <= min(now, cutoff + datetime.timedelta(hours=1))]
    if not points:
        return {"day": day, "followers": None, "at": None, "provisional": True}
    at, n = max(points)
    on_time = abs(at - cutoff) <= datetime.timedelta(hours=1)
    return {"day": day, "followers": n, "at": at.isoformat(),
            "provisional": not on_time}


def own_posts() -> list[dict]:
    """Every post of ours with a time: replies from the radar's decisions and
    the reply series, digests, syndication hooks and proof posts from the post
    ledger. Thread parts are left out — they are the same post."""
    out: dict[str, dict] = {}
    try:
        with open(RADAR_STATE) as f:
            decisions = (json.load(f).get("decisions") or {}).values()
    except (OSError, ValueError):
        # The reply series below still carries every measured reply.
        decisions = []
    for d in decisions:
        if d.get("result") == "posted" and d.get("reply_id"):
            at = _ts(d.get("posted_at"))
            if at:
                out[d["reply_id"]] = {"at": at, "kind": "reply",
                                      "tweet_id": d["reply_id"]}
    for r in rows("reply"):
        at = _ts(r.get("posted_at"))
        if at and r.get("tweet_id") and r["tweet_id"] not in out:
            out[r["tweet_id"]] = {"at": at, "kind": "reply", "tweet_id": r["tweet_id"]}
    try:
        with open(POSTS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("kind") in ("digest", "syndication", "proof") and r.get("id"):
                    at = _ts(r.get("at"))
                    if at:
                        out.setdefault(r["id"], {"at": at, "kind": r["kind"],
                                                 "tweet_id": r["id"]})
    except OSError:
        pass
    return sorted(out.values(), key=lambda p: p["at"])


def follower_steps(since: str, now: datetime.datetime) -> list[dict]:
    """Every rise in the count since `since`, with our posts in the 24 hours
    before it was seen.

    Hourly points where the hourly series exists, the daily 18:00 points
    before it. A rise between two daily points is labelled as day resolution:
    it happened somewhere in those 24 hours.
    """
    start = datetime.datetime.fromisoformat(since + "T00:00:00+00:00")
    hourly = [h for h in hourly_followers() if h["at"] <= now]
    first_hour = hourly[0]["at"] if hourly else None
    points = [{"at": at, "followers": n, "res": "Tag", "new": None}
              for at, n in sorted(daily_followers().values())
              if at <= now and (first_hour is None or at < first_hour)]
    points += [{"at": h["at"], "followers": h["followers"], "res": "Stunde",
                "new": h["new"]} for h in hourly]
    posts = own_posts()
    out = []
    for prev, cur in zip(points, points[1:]):
        # Both ends inside the window: the rise from 03.10 18:00 to 04.10
        # 18:00 is the day before the window, not a day in it.
        if prev["at"] < start or cur["followers"] <= prev["followers"]:
            continue
        lo = cur["at"] - datetime.timedelta(hours=24)
        out.append({"at": cur["at"].isoformat(), "from": prev["followers"],
                    "to": cur["followers"], "resolution": cur["res"],
                    "new_ids": cur["new"],
                    "posts": [{"kind": p["kind"], "tweet_id": p["tweet_id"],
                               "at": p["at"].isoformat(),
                               "hours_before": round((cur["at"] - p["at"])
                                                     .total_seconds() / 3600, 1)}
                              for p in posts if lo <= p["at"] <= cur["at"]]})
    return out


# ── Three series, one basis ──

SERIES = (("reply", "Replies"), ("digest", "Digest"), ("syndication", "Syndication"))


def side_by_side(since: str) -> dict:
    out = {}
    for kind, _ in SERIES:
        rs = latest_per_post(kind, since)
        impr = [r["impressions"] for r in rs if isinstance(r.get("impressions"), int)]
        clicks = [r["profile_clicks"] for r in rs
                  if isinstance(r.get("profile_clicks"), int)]
        likes = [r["likes"] for r in rs if isinstance(r.get("likes"), int)]
        ages = [r["age_hours"] for r in rs if isinstance(r.get("age_hours"), (int, float))]
        out[kind] = {
            "posts": len(rs),
            "impressions_median": statistics.median(impr) if impr else None,
            "impressions_sum": sum(impr) if impr else None,
            "clicks_sum": sum(clicks) if clicks else None,
            "clicks_known": len(clicks),
            # Per post over the posts that carry the column. A digest
            # measured before 2026-10-12 has no click figure; counting it as
            # zero would lower the digest's rate by an absence.
            "clicks_per_post": round(sum(clicks) / len(clicks), 2) if clicks else None,
            "likes_sum": sum(likes) if likes else None,
            "age_median_h": round(statistics.median(ages)) if ages else None,
        }
    return out


def collect(since: str = WINDOW_START,
            now: datetime.datetime | None = None) -> dict:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    k = _collect(since)
    until = min(now.strftime("%Y-%m-%d"), DECISION_DATE)
    k["costs"] = costs(since, until)
    k["followers"] = {"start": followers_on(WINDOW_START, now),
                      "decision": followers_on(DECISION_DATE, now),
                      "daily": {d: n for d, (_, n) in sorted(daily_followers().items())
                                if d >= since}}
    k["follower_steps"] = follower_steps(since, now)
    k["side_by_side"] = side_by_side(since)
    return k


def _collect(since: str = WINDOW_START) -> dict:
    replies = latest_per_post("reply")
    in_window = [r for r in replies if (r.get("posted_at") or "") >= since]
    before = [r for r in replies if (r.get("posted_at") or "") < since]
    digests = [r for r in latest_per_post("digest")
               if (r.get("posted_at") or "") >= since]

    def agg(rs, field):
        vals = [r.get(field) for r in rs if isinstance(r.get(field), int)]
        return {"n": len(vals), "sum": sum(vals),
                "median": statistics.median(vals) if vals else None,
                "max": max(vals) if vals else None} if vals else {
                    "n": 0, "sum": 0, "median": None, "max": None}

    return {
        "since": since, "decision_date": DECISION_DATE,
        "by_prompt": by_prompt(),
        "replies_in_window": [_shape(r) for r in in_window],
        "replies_before": [_shape(r) for r in before],
        "window": {f: agg(in_window, f) for f in
                   ("impressions", "profile_clicks", "likes", "engagements")},
        "retro": {f: agg(before, f) for f in
                  ("impressions", "profile_clicks", "likes", "engagements")},
        "digest": {f: agg(digests, f) for f in ("impressions", "likes")},
        "digest_n": len(digests),
    }


def _shape(r: dict) -> dict:
    d24, at = delta_24h(r.get("tweet_id", ""))
    return {"tweet_id": r.get("tweet_id"), "posted_at": r.get("posted_at"),
            "route": r.get("route"), "impressions": r.get("impressions"),
            "likes": r.get("likes"), "profile_clicks": r.get("profile_clicks"),
            "engagements": r.get("engagements"),
            "target_impressions": r.get("target_impressions"),
            "followers_delta_24h": d24, "delta_taken_at_hours": at}


def format_report(k: dict) -> str:
    L = [f"🔁 <b>Reply-Wirkung</b> — Fenster ab {k['since']}, "
         f"Entscheidung {k['decision_date']}", ""]
    for label, key, rs in (("Im Fenster", "window", k["replies_in_window"]),
                           ("Rückwirkend (vor dem Fenster)", "retro",
                            k["replies_before"])):
        if not rs:
            L += [f"<b>{label}</b>: keine", ""]
            continue
        L.append(f"<b>{label}</b> — {len(rs)} Replies")
        L.append("<pre>")
        L.append("Datum       Impr  Likes  Klicks  Ziel-Impr  Δ24h")
        for r in rs:
            d = ("—" if r["followers_delta_24h"] is None
                 else f"{r['followers_delta_24h']:+d}@{r['delta_taken_at_hours']:.0f}h")
            L.append(f"{(r['posted_at'] or '?')[:10]}  {_n(r['impressions']):>4}  "
                     f"{_n(r['likes']):>5}  {_n(r['profile_clicks']):>6}  "
                     f"{_n(r['target_impressions']):>9}  {d}")
        L.append("</pre>")
        a = k[key]
        L.append(f"Summe: {a['impressions']['sum']} Impressionen · "
                 f"{a['profile_clicks']['sum']} Profilklicks · "
                 f"{a['likes']['sum']} Likes")
        if a["impressions"]["n"]:
            L.append(f"Median je Reply: {a['impressions']['median']:.0f} Impr · "
                     f"max {a['impressions']['max']}")
        L.append("")

    d = k["digest"]
    if k["digest_n"]:
        L += [f"<b>Digest im selben Fenster</b> — {k['digest_n']} Posts, "
              f"Median {d['impressions']['median']:.0f} Impr je Post", ""]
    else:
        L += ["<b>Digest im selben Fenster</b>: keine Messung", ""]

    bp = k.get("by_prompt") or {}
    if bp:
        L += ["<b>Entwürfe je Kandidat, getrennt nach Prompt-Version</b>",
              "<pre>",
              "Tag         Version          Läufe  Kand  Entw  Gate  Quote"]
        for day in sorted(bp):
            for ver, r in sorted(bp[day].items()):
                cand = r.get("candidates") or 0
                rate = (r.get("drafts", 0) / cand * 100) if cand else 0
                L.append("%-10s  %-15s %5d %5d %5d %5d %5.0f %%"
                         % (day, ver, r.get("runs", 0), cand,
                            r.get("drafts", 0), r.get("gate_pass", 0), rate))
        L += ["</pre>", ""]
    L += format_extensions(k)
    v = verdict(k)
    L += ["<b>Schwelle, festgeschrieben am 2026-10-04</b>",
          f"Median Impressionen je Reply: <b>{_n(v['median_impressions'])}</b> "
          f"gegen ≥ {MEDIAN_IMPRESSIONS_MIN} → "
          f"{'erfüllt' if v['median_ok'] else 'nicht erfüllt'}",
          f"Profilklicks je Reply: <b>{_n(v['clicks_per_reply'])}</b> "
          f"gegen ≥ {CLICKS_PER_REPLY_MIN} → "
          f"{'erfüllt' if v['clicks_ok'] else 'nicht erfüllt'}",
          f"<b>{'Fortführung' if v['continue'] else 'Einstellung'}</b>"
          + ("" if v["continue"] else " — Radar abschalten, Budget auf Null")
          + (f" ({v['replies']} Replies im Fenster)" if v["replies"]
             else " (keine Replies im Fenster — das ist kein Bestehen)"),
          "",
          "<b>Was die Zahlen tragen</b>",
          "Impressionen und Profilklicks sind je Reply belastbar. Der "
          "Follower-Δ24h ist es nicht: sieben Replies und der Digest teilen "
          "einen Followerstand, ein Follower an einem Tag mit drei Posts "
          "gehört keinem davon. Steht als Spalte da, weil er beauftragt ist, "
          "nie als entscheidende Zahl.",
          "",
          "Entscheidungsgrundlage am " + k["decision_date"] + ": Impressionen "
          "und Profilklicks je Reply gegen dieselben zwei der Digest-Reihe im "
          "selben Zeitraum."]
    return "\n".join(L)


def format_extensions(k: dict) -> list[str]:
    """The three series side by side, the costs, and the follower figures.
    Context for the decision; the threshold below does not read any of it."""
    L: list[str] = []
    sbs = k.get("side_by_side")
    if sbs:
        L += ["<b>Drei Reihen im selben Fenster</b> (letzte Messung je Post)",
              "<pre>",
              "Reihe        Posts  Impr-Med  Impr  Klicks  Kl/Post  Likes  Alter"]
        for kind, label in SERIES:
            x = sbs[kind]
            clicks = ("—" if x["clicks_sum"] is None
                      else f"{x['clicks_sum']}/{x['clicks_known']}")
            L.append("%-12s %5d  %8s  %4s  %6s  %7s  %5s  %4sh"
                     % (label, x["posts"], _n(x["impressions_median"]),
                        _n(x["impressions_sum"]), clicks, _n(x["clicks_per_post"]),
                        _n(x["likes_sum"]), _n(x["age_median_h"])))
        L += ["</pre>",
              "Klicks a/b: Summe über b Posts, die die Spalte führen. Digest und "
              "Syndication führen Profilklicks erst seit 2026-10-12, ältere Posts "
              "zählen dort nicht mit. Alter: Median der Stunden zwischen Post und "
              "letzter Messung — ein Digest, der nur bei +6h gemessen wurde, steht "
              "gegen Replies mit bis zu 14 Tagen.", ""]

    c = k.get("costs")
    if c:
        L += ["<b>Kosten im Fenster</b>", "<pre>",
              "Tag         X gesamt  X Radar  Modell Radar"]
        for r in c["days"]:
            m = "—" if r["model_radar"] is None else f"{r['model_radar']:.2f}"
            L.append("%-10s  %8.2f  %7.2f  %12s" % (r["day"], r["x_total"],
                                                   r["x_radar"], m))
        L.append("</pre>")
        n = len(k.get("replies_in_window") or [])
        clicks = (k.get("window") or {}).get("profile_clicks", {}).get("sum") or 0
        L.append(f"Radar gesamt: {c['radar_usd']:.2f} $ · X gesamt (alle Pfade): "
                 f"{c['x_total_usd']:.2f} $")
        L.append(f"Je Reply: {_usd(c['radar_usd'], n)} · je Profilklick: "
                 f"{_usd(c['radar_usd'], clicks)}")
        if c.get("model_floor"):
            L.append(f"Modellkosten stehen erst ab {TOKENS_LOGGED_SINCE} im Log. "
                     "Davor fehlen sie, die Radar-Summe ist eine Untergrenze.")
        L.append("")

    f = k.get("followers")
    if f:
        def one(x):
            if x["followers"] is None:
                return "keine Messung"
            s = f"{x['followers']} (Stand {x['at'][:16].replace('T', ' ')} UTC)"
            if x["provisional"]:
                s += " — vorläufig, die 18:00-Messung dieses Tages liegt noch nicht vor"
            return s
        L += ["<b>Follower</b>",
              f"{f['start']['day']}: {one(f['start'])}",
              f"{f['decision']['day']}: {one(f['decision'])}"]
        if f["daily"]:
            L.append("Täglich 18:00: " + " · ".join(
                f"{d[5:]} {n}" for d, n in f["daily"].items()))
        L.append("")

    steps = k.get("follower_steps")
    if steps is not None:
        L.append("<b>Follower nach Posts</b>")
        if not steps:
            L.append("Kein Anstieg im Fenster.")
        for s in steps:
            head = (f"{s['at'][:16].replace('T', ' ')}: {s['from']} → {s['to']} "
                    f"({s['resolution']}")
            if s["new_ids"]:
                head += f", {s['new_ids']} neue IDs"
            L.append(head + ")")
            if not s["posts"]:
                L.append("  keine eigenen Posts in den 24 h davor")
            for p in s["posts"]:
                L.append(f"  {p['kind']} {p['at'][:16].replace('T', ' ')} "
                         f"({p['hours_before']:.0f} h davor)")
        L += ["Das ist Zeitfolge, keine Zuordnung. Die Zahlen sind netto: ein "
              "Entfolgen im selben Zeitraum verdeckt einen Zugang. Bei "
              "Tagesauflösung liegt der Zugang irgendwo in den 24 h vor der "
              "Messung.", ""]
    return L


def _usd(total: float, n: int) -> str:
    return f"{total / n:.2f} $" if n else "— (keine)"


def _n(v) -> str:
    return "—" if v is None else str(v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=WINDOW_START)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    k = collect(a.since)
    print(json.dumps(k, indent=1, ensure_ascii=False) if a.json
          else format_report(k))
    if a.send:
        notify.send_telegram(format_report(k), channel=notify.STATS,
                             parse_mode="HTML")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
