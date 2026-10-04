"""Measure the daily digest six hours after it posts, and the replies with it.

Cron: 18:00 UTC daily, six hours after the 12:00 digest. Reads the tweet id
herald_v3 stored, asks X for its public metrics, appends one line to
data/digest_metrics.jsonl and sends the figures to Telegram.

The file is the point. Impressions are only worth anything as a series, and a
number that lives in a chat message is gone by the following week; sm_kpis.py
reads this file for the weekly top-post figure.

Two kinds of row live in it, told apart by `kind`:

  digest — one post of ours, measured once at +6h.
  reply  — one reply of ours, measured every day for two weeks, alongside the
           post it answered. A reply is worth measuring against its target:
           500 impressions under a post with 400 is a different result from
           500 under one with 200,000.

Rows written before 2026-09-22 carry no `kind` and are digests; every reader
here treats a missing `kind` as "digest" rather than rewriting the file.

    python scripts/digest_metrics.py              # the scheduled run
    python scripts/digest_metrics.py --quiet      # no Telegram, still writes
    python scripts/digest_metrics.py --tweet-id N # measure one specific post
    python scripts/digest_metrics.py --replies-only
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from requests_oauthlib import OAuth1

from app import notify
from agents import x_meter

DATA_DIR = os.path.expanduser("~/moltstack/data")
HERALD_STATE = os.path.join(DATA_DIR, "herald_state.json")
RADAR_STATE = os.path.join(DATA_DIR, "reply_radar_state.json")
METRICS_FILE = os.path.join(DATA_DIR, "digest_metrics.jsonl")
TWEETS_URL = "https://api.twitter.com/2/tweets"
ME_URL = "https://api.twitter.com/2/users/me"

# How long a reply stays in the daily measurement. Impressions on a reply are
# effectively done inside a week; two weeks is enough to see that and to catch
# the occasional one that gets picked up late.
REPLY_TRACK_DAYS = 14

# The window a measurement counts as "24 hours after the post". Twelve to
# thirty-six hours, because the series is measured roughly daily and at
# irregular times, so the nearest row to +24 h lands somewhere in there.
#
# The first version allowed up to 48 h and promptly stamped a delta taken at
# 47.3 h as the 24-hour figure — the same mislabelling this file objects to
# everywhere else. Narrower produces nulls instead, and a null is honest.
# `delta_24h_taken_at_hours` travels with the number regardless, so a reader
# never has to trust the label alone.
DELTA_WINDOW = (12.0, 36.0)

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("digest_metrics")
notify.silence_http_request_logs()


def x_auth() -> OAuth1 | None:
    keys = [os.getenv(k, "") for k in
            ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    return OAuth1(*keys) if all(keys) else None


def fetch_metrics(tweet_id: str) -> dict | None:
    auth = x_auth()
    if not auth:
        log.error("X credentials not available")
        return None
    try:
        r = requests.get(f"{TWEETS_URL}/{tweet_id}",
                         params={"tweet.fields": "created_at,public_metrics,text"},
                         auth=auth, timeout=30)
    except Exception as e:
        log.error(f"X request failed: {e}")
        return None
    if r.status_code != 200:
        log.error(f"X API {r.status_code}: {r.text[:300]}")
        return None
    data = r.json().get("data")
    if not data:
        log.error(f"No data for tweet {tweet_id}: {r.text[:200]}")
        return None
    return data


def fetch_many(ids: list[str], auth) -> dict[str, dict]:
    """Metrics for up to 100 posts in one call, keyed by id.

    A post that was deleted or hidden comes back in `errors`, not in `data`, so
    it is simply absent here — the caller sees a missing key, not a wrong zero.
    """
    if not ids:
        return {}
    try:
        r = requests.get(TWEETS_URL,
                         params={"ids": ",".join(ids[:100]),
                                 # non_public_metrics carries user_profile_clicks
                                 # and engagements, and only for our own posts —
                                 # a target post we did not write comes back
                                 # without them, which is why the reply row
                                 # reads them off the reply and never the target.
                                 # Same request, same resources, same bill:
                                 # X charges per object returned, not per field.
                                 "tweet.fields": "created_at,public_metrics,"
                                                 "non_public_metrics"},
                         auth=auth, timeout=30)
    except Exception as e:
        log.error(f"X request failed: {e}")
        return {}
    if r.status_code != 200:
        log.error(f"X API {r.status_code}: {r.text[:300]}")
        return {}
    body = r.json()
    x_meter.record_read(body, source="tweets-batch")
    return {d["id"]: d for d in body.get("data", []) or []}


def followers(auth) -> int | None:
    try:
        r = requests.get(ME_URL, params={"user.fields": "public_metrics"},
                         auth=auth, timeout=30)
        if r.status_code == 200:
            return r.json()["data"]["public_metrics"]["followers_count"]
    except Exception as e:
        log.warning(f"follower count unavailable: {type(e).__name__}")
    return None


def tracked_replies(now: datetime.datetime) -> list[dict]:
    """Replies still inside the measurement window, from the radar's own state.

    Both routes end up here: a mention answered through the API, and a list
    draft posted by hand and then recognised on our timeline. `route` keeps
    them apart in the file, because which of the two produced a reply is part
    of what this measures.
    """
    try:
        with open(RADAR_STATE) as f:
            state = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning(f"Cannot read {RADAR_STATE}: {e}")
        return []
    out = []
    for target_id, d in (state.get("decisions") or {}).items():
        if d.get("result") != "posted" or not d.get("reply_id"):
            continue
        posted_at = d.get("posted_at")
        if posted_at:
            try:
                posted = datetime.datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                if (now - posted).days > REPLY_TRACK_DAYS:
                    continue
            except ValueError:
                pass
        out.append({"target_id": target_id, "reply_id": d["reply_id"],
                    "route": d.get("route", "api"),
                    "followers_at_post": d.get("followers_at_post"),
                    "link": d.get("link")})
    return out


def run_all(tweet_id: str | None = None, quiet: bool = False) -> int:
    """The digest and every tracked reply, measured in one request.

    Before this the daily run made one call for the digest and two more for the
    replies. GET /2/tweets?ids=… takes a hundred ids, and a metered API charges
    per request, not per id — so three became one and the numbers are
    identical.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    auth = x_auth()
    if not auth:
        log.error("X credentials not available")
        return 1

    digest_id, digest_date = (tweet_id, None)
    if not digest_id:
        digest_id, digest_date = last_digest()
    tracked = tracked_replies(now)
    clips = tracked_videos(now)

    ids = [i for i in [digest_id] if i]
    ids += [t["reply_id"] for t in tracked] + [t["target_id"] for t in tracked]
    ids += [c["x"]["post"] for c in clips if (c.get("x") or {}).get("post")]
    # Order-preserving dedup: a reply that answers the digest would otherwise
    # be asked for twice in the same call.
    seen, unique = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    metrics = fetch_many(unique, auth)
    log.info(f"one call for {len(unique)} posts: "
             f"{'digest + ' if digest_id else ''}{len(tracked)} replies, "
             f"{len(clips)} clips")
    # One follower read for all three series. Another $0.010 per series would
    # buy the same number three times.
    x_followers = followers(auth)

    codes = []
    if digest_id:
        codes.append(write_digest_row(digest_id, digest_date,
                                      metrics.get(digest_id), now, quiet,
                                      followers_now=x_followers))
    else:
        log.error("No digest tweet id to measure")
        codes.append(1)
    # One series failing must not stop the others; each is independent.
    codes.append(write_reply_rows(tracked, metrics, auth, now, quiet))
    codes.append(write_video_rows(clips, metrics, x_followers, now, quiet))
    return max(codes)


def measure_replies(quiet: bool = False) -> int:
    """One row per tracked reply, plus a Telegram line if anything moved."""
    now = datetime.datetime.now(datetime.timezone.utc)
    tracked = tracked_replies(now)
    if not tracked:
        log.info("No replies inside the tracking window")
        return 0
    auth = x_auth()
    if not auth:
        log.error("X credentials not available")
        return 1

    ids = [t["reply_id"] for t in tracked] + [t["target_id"] for t in tracked]
    return write_reply_rows(tracked, fetch_many(ids, auth), auth, now, quiet)


def write_reply_rows(tracked: list[dict], metrics: dict, auth,
                     now: datetime.datetime, quiet: bool) -> int:
    if not tracked:
        return 0
    now_followers = followers(auth)

    lines = []
    for t in tracked:
        reply = metrics.get(t["reply_id"])
        if not reply:
            log.warning(f"reply {t['reply_id']} not readable — skipped")
            continue
        target = metrics.get(t["target_id"], {})
        pm = reply.get("public_metrics", {})
        tpm = target.get("public_metrics", {})
        base = t["followers_at_post"]
        age_h = None
        if reply.get("created_at"):
            posted = datetime.datetime.fromisoformat(
                reply["created_at"].replace("Z", "+00:00"))
            age_h = round((now - posted).total_seconds() / 3600, 1)
        npm = reply.get("non_public_metrics") or {}
        row = {
            "kind": "reply",
            "measured_at": now.isoformat(),
            "tweet_id": t["reply_id"],
            "target_id": t["target_id"],
            "route": t["route"],
            "posted_at": reply.get("created_at"),
            "age_hours": age_h,
            "impressions": pm.get("impression_count"),
            "likes": pm.get("like_count"),
            "replies": pm.get("reply_count"),
            "retweets": pm.get("retweet_count"),
            # The two that say whether a reply did anything for us rather than
            # for the thread. Verified against the live API on 2026-10-04 before
            # the column existed, because a metric one assumes is available is
            # worth less than a field that merely exists.
            "profile_clicks": npm.get("user_profile_clicks"),
            "engagements": npm.get("engagements"),
            "target_impressions": tpm.get("impression_count"),
            "followers_at_post": base,
            "followers_now": now_followers,
            "followers_delta": (None if base is None or now_followers is None
                                else now_followers - base),
        }
        # The 24 h delta, written once, at the first measurement inside the
        # window — and with the age it was actually taken at, because "24 h"
        # measured at 31 h is a different number and the reader has to see which.
        #
        # It is the weakest column here by construction: seven replies and the
        # digest share one follower count, so a follower gained on a day with
        # three posts cannot be attributed to any of them. Recorded because it
        # was asked for, and labelled so the 18.10 decision does not rest on it.
        if age_h is not None and DELTA_WINDOW[0] <= age_h <= DELTA_WINDOW[1]:
            row["followers_delta_24h"] = row["followers_delta"]
            row["delta_24h_taken_at_hours"] = age_h
        append(row)
        log.info(json.dumps(row))
        lines.append(f"· {row['impressions']} Impr · {row['likes']} Likes · "
                     f"{row['profile_clicks']} Profilklicks "
                     f"(Ziel {row['target_impressions']}) · {t['route']}\n"
                     f"  {t['link'] or t['reply_id']}")

    if lines and not quiet:
        notify.send_telegram(
            f"\U0001f501 Replies ({len(lines)} in Messung, {REPLY_TRACK_DAYS} Tage)\n"
            + "\n".join(lines)
            + (f"\n\nFollower jetzt: {now_followers}"
               if now_followers is not None else ""),
            channel=notify.STATS)
    return 0


VIDEO_LEDGER = os.path.join(DATA_DIR, "video_posts.jsonl")
VIDEO_TRACK_DAYS = 14
BSKY_PUBLIC = "https://public.api.bsky.app/xrpc"


def tracked_videos(now: datetime.datetime) -> list[dict]:
    rows = []
    try:
        with open(VIDEO_LEDGER) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                at = _parsed(row.get("at") or "")
                if at and (now - at).days > VIDEO_TRACK_DAYS:
                    continue
                rows.append(row)
    except FileNotFoundError:
        return []
    return rows


def _parsed(stamp: str):
    try:
        when = datetime.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def bsky_counts(uri: str) -> dict:
    """Likes, reposts and replies for one Bluesky post.

    No impressions: the AppView does not expose a view count at all, so the
    networks are not comparable on reach and the row says so rather than
    carrying a zero that would read as none.
    """
    try:
        r = requests.get(f"{BSKY_PUBLIC}/app.bsky.feed.getPosts",
                         params={"uris": uri}, timeout=30)
        if r.status_code != 200:
            log.error(f"bluesky getPosts {r.status_code}: {r.text[:160]}")
            return {}
        posts = r.json().get("posts") or []
        if not posts:
            return {}
        p = posts[0]
        return {"likes": p.get("likeCount"), "reposts": p.get("repostCount"),
                "replies": p.get("replyCount"), "quotes": p.get("quoteCount"),
                "impressions": None}
    except Exception as e:
        log.error(f"bluesky read failed: {type(e).__name__}: {e}")
        return {}


def bsky_followers(handle: str = "moltrust.ch") -> int | None:
    try:
        r = requests.get(f"{BSKY_PUBLIC}/app.bsky.actor.getProfile",
                         params={"actor": handle}, timeout=30)
        if r.status_code == 200:
            return r.json().get("followersCount")
    except Exception as e:
        log.warning(f"bluesky profile unavailable: {type(e).__name__}")
    return None


def measure_video(quiet: bool = False) -> int:
    """The video series on its own, for --video-only."""
    now = datetime.datetime.now(datetime.timezone.utc)
    clips = tracked_videos(now)
    if not clips:
        log.info("No videos inside the tracking window")
        return 0
    auth = x_auth()
    if not auth:
        log.error("X credentials not available")
        return 1
    x_ids = [c["x"]["post"] for c in clips if (c.get("x") or {}).get("post")]
    return write_video_rows(clips, fetch_many(x_ids, auth) if x_ids else {},
                            followers(auth), now, quiet)


def write_video_rows(clips: list[dict], metrics: dict, x_followers,
                     now: datetime.datetime, quiet: bool) -> int:
    """One row per clip per day, both networks kept apart.

    Apart on purpose: X reports impressions and Bluesky reports none, so a
    single combined figure would be X's number wearing both names.
    """
    if not clips:
        return 0
    b_followers = bsky_followers()

    lines = []
    for c in clips:
        xp = (c.get("x") or {}).get("post")
        bu = (c.get("bluesky") or {}).get("uri")
        xm = (metrics.get(xp, {}) or {}).get("public_metrics", {}) if xp else {}
        bm = bsky_counts(bu) if bu else {}
        posted = _parsed(c.get("at") or "")
        age_h = round((now - posted).total_seconds() / 3600, 1) if posted else None
        row = {
            "kind": "video",
            "measured_at": now.isoformat(),
            "clip": c.get("clip"),
            "posted_at": c.get("at"),
            "age_hours": age_h,
            "x": {"tweet_id": xp, "url": (c.get("x") or {}).get("url"),
                  "impressions": xm.get("impression_count"),
                  "likes": xm.get("like_count"),
                  "reposts": xm.get("retweet_count"),
                  "replies": xm.get("reply_count"),
                  "quotes": xm.get("quote_count"),
                  "followers_now": x_followers},
            "bluesky": {"uri": bu, "url": (c.get("bluesky") or {}).get("url"),
                        "impressions": None, "likes": bm.get("likes"),
                        "reposts": bm.get("reposts"), "replies": bm.get("replies"),
                        "quotes": bm.get("quotes"),
                        "followers_now": b_followers},
            "linkedin": c.get("linkedin"),
        }
        append(row)
        log.info(json.dumps(row))
        lines.append(
            f"· {c.get('clip')}  +{age_h}h\n"
            f"  X:       {row['x']['impressions']} Impr · {row['x']['likes']} Likes · "
            f"{row['x']['reposts']} RP · {row['x']['replies']} Repl\n"
            f"  Bluesky: — Impr (nicht veröffentlicht) · {row['bluesky']['likes']} Likes · "
            f"{row['bluesky']['reposts']} RP · {row['bluesky']['replies']} Repl")

    if lines and not quiet:
        notify.send_telegram(
            f"\U0001f3a5 Video-Reihe ({len(lines)} Clip(s), {VIDEO_TRACK_DAYS} Tage)\n"
            + "\n".join(lines)
            + f"\n\nFollower: X {x_followers} · Bluesky {b_followers}",
            channel=notify.STATS)
    return 0


def compare_video_digest(days: int = 7, quiet: bool = False) -> int:
    """Impressions per post and follower delta per post, video against digest.

    Per post, not in total: one video and seven digests in the same week would
    otherwise make the digest look seven times better at being watched.

    Only X is compared. Bluesky publishes no view count, so there is nothing on
    that side to put next to an impression figure — the Bluesky numbers stay in
    the series and out of this comparison.
    """
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    latest: dict[tuple, dict] = {}
    try:
        with open(METRICS_FILE) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = row.get("kind", "digest")
                if kind not in ("video", "digest"):
                    continue
                when = _parsed(row.get("measured_at") or "")
                if not when or when < since:
                    continue
                # One post measured daily would otherwise count as seven posts.
                ident = row.get("clip") or row.get("tweet_id")
                key = (kind, ident)
                prev = latest.get(key)
                if not prev or (_parsed(prev.get("measured_at") or "") or since) < when:
                    latest[key] = row
    except FileNotFoundError:
        print("no metrics file yet")
        return 1

    def side(kind: str) -> dict:
        rows = [r for (k, _), r in latest.items() if k == kind]
        if not rows:
            return {"posts": 0}
        if kind == "video":
            impr = [r["x"].get("impressions") for r in rows
                    if (r.get("x") or {}).get("impressions") is not None]
            foll = [r["x"].get("followers_now") for r in rows
                    if (r.get("x") or {}).get("followers_now") is not None]
        else:
            impr = [r.get("impressions") for r in rows
                    if r.get("impressions") is not None]
            foll = [r.get("followers_now") for r in rows
                    if r.get("followers_now") is not None]
        return {"posts": len(rows),
                "impressions_total": sum(impr) if impr else None,
                "impressions_per_post": round(sum(impr) / len(impr), 1) if impr else None,
                "followers_seen": max(foll) if foll else None}

    v, d = side("video"), side("digest")
    lines = [f"\U0001f4ca Video gegen Digest, {days} Tage (nur X)", ""]
    for label, x in (("Video", v), ("Digest", d)):
        if not x["posts"]:
            lines.append(f"{label}: keine Posts im Fenster")
            continue
        lines.append(f"{label}: {x['posts']} Post(s) · "
                     f"{x['impressions_total']} Impressionen · "
                     f"{x['impressions_per_post']} pro Post")
    if v.get("impressions_per_post") and d.get("impressions_per_post"):
        ratio = v["impressions_per_post"] / d["impressions_per_post"]
        lines.append("")
        lines.append(f"Video erreicht {ratio:.2f}× die Impressionen eines Digests "
                     f"pro Post.")
    lines.append("")
    lines.append(f"Follower-Spalte: beide Reihen führen sie seit "
                 f"{DIGEST_FOLLOWERS_SINCE}. Ältere Digest-Zeilen haben sie "
                 f"nicht — das ist eine Lücke, keine Null, und wird nicht "
                 f"rekonstruiert.")
    if v.get("followers_seen") and d.get("followers_seen"):
        lines.append(f"Stand bei der letzten Messung: Video "
                     f"{v['followers_seen']}, Digest {d['followers_seen']}.")
    lines.append("Bluesky bleibt aus diesem Vergleich: die AppView "
                 "veröffentlicht keine Impressionen. Likes, Reposts, Replies "
                 "und Follower stehen in der Video-Reihe und im Wochenreport "
                 "als eigene Zeile.")

    text = "\n".join(lines)
    print(text)
    if not quiet:
        notify.send_telegram(text, channel=notify.STATS)
    return 0


def last_digest() -> tuple[str | None, str | None]:
    """(tweet_id, date) of the most recent digest, from herald_v3's own state."""
    try:
        with open(HERALD_STATE) as f:
            state = json.load(f)
    except Exception as e:
        log.error(f"Cannot read {HERALD_STATE}: {e}")
        return None, None
    if state.get("last_mode") != "digest":
        log.warning(f"Last herald post was mode={state.get('last_mode')}, not a digest")
    return state.get("last_tweet_id"), state.get("last_digest_date")


def append(row: dict) -> None:
    try:
        with open(METRICS_FILE, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        os.chmod(METRICS_FILE, 0o640)
    except Exception as e:
        log.error(f"Cannot append to {METRICS_FILE}: {e}")


def run(tweet_id: str | None = None, quiet: bool = False) -> int:
    """Measure one digest on its own. Kept for --tweet-id; the daily run uses
    run_all, which asks for everything in a single request."""
    now = datetime.datetime.now(datetime.timezone.utc)
    digest_date = None
    if not tweet_id:
        tweet_id, digest_date = last_digest()
    if not tweet_id:
        log.error("No digest tweet id to measure")
        return 1
    return write_digest_row(tweet_id, digest_date, fetch_metrics(tweet_id), now,
                            quiet, followers_now=followers(x_auth()))


# The digest series started carrying a follower count on this date. Before it
# the column does not exist, and a report that filled the gap with zero would
# read as "nobody followed us then".
DIGEST_FOLLOWERS_SINCE = "2026-10-02"


def write_digest_row(tweet_id: str, digest_date: str | None, data: dict | None,
                     now: datetime.datetime, quiet: bool,
                     followers_now: int | None = None) -> int:
    if not data:
        log.error(f"No metrics for digest {tweet_id}")
        return 1

    pm = data.get("public_metrics", {})
    posted_at = data.get("created_at")
    age_h = None
    if posted_at:
        posted = datetime.datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        age_h = round((now - posted).total_seconds() / 3600, 1)

    row = {
        "kind": "digest",
        "measured_at": now.isoformat(),
        "tweet_id": tweet_id,
        "digest_date": digest_date,
        "posted_at": posted_at,
        "age_hours": age_h,
        "impressions": pm.get("impression_count"),
        "likes": pm.get("like_count"),
        "replies": pm.get("reply_count"),
        "retweets": pm.get("retweet_count"),
        "quotes": pm.get("quote_count"),
        "bookmarks": pm.get("bookmark_count"),
        # The same column and the same reading as the video series: the count at
        # measurement, from the one follower read the daily run already makes.
        # A true at-posting figure would need a read inside herald_v3, which is
        # a second billed request for a number that moves by ones.
        "followers_now": followers_now,
    }
    append(row)
    log.info(json.dumps(row))

    url = f"https://x.com/MolTrust/status/{tweet_id}"
    msg = (f"\U0001f4c8 Digest at +{age_h}h\n{url}\n\n"
           f"{row['impressions']} impressions · {row['likes']} likes · "
           f"{row['replies']} replies · {row['retweets']} RT · "
           f"{row['bookmarks']} bookmarks")
    print(msg)
    if not quiet:
        notify.send_telegram(msg, channel=notify.STATS)
    return 0


if __name__ == "__main__":
    tid = None
    if "--tweet-id" in sys.argv:
        i = sys.argv.index("--tweet-id")
        tid = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    quiet = "--quiet" in sys.argv
    if "--replies-only" in sys.argv:
        sys.exit(measure_replies(quiet=quiet))
    if "--video-only" in sys.argv:
        sys.exit(measure_video(quiet=quiet))
    if "--compare-video" in sys.argv:
        n = 7
        if "--days" in sys.argv:
            i = sys.argv.index("--days")
            if i + 1 < len(sys.argv):
                n = int(sys.argv[i + 1])
        sys.exit(compare_video_digest(days=n, quiet=quiet))
    # One call a day, not one per post. The digest and every tracked reply are
    # asked for together: GET /2/tweets?ids=… takes a hundred at a time, and
    # the metered API charges per request.
    sys.exit(run_all(tweet_id=tid, quiet=quiet))
