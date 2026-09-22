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
                                 "tweet.fields": "created_at,public_metrics"},
                         auth=auth, timeout=30)
    except Exception as e:
        log.error(f"X request failed: {e}")
        return {}
    if r.status_code != 200:
        log.error(f"X API {r.status_code}: {r.text[:300]}")
        return {}
    return {d["id"]: d for d in r.json().get("data", []) or []}


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
    metrics = fetch_many(ids, auth)
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
            "target_impressions": tpm.get("impression_count"),
            "followers_at_post": base,
            "followers_now": now_followers,
            "followers_delta": (None if base is None or now_followers is None
                                else now_followers - base),
        }
        append(row)
        log.info(json.dumps(row))
        lines.append(f"· {row['impressions']} Impr · {row['likes']} Likes "
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
    now = datetime.datetime.now(datetime.timezone.utc)
    digest_date = None
    if not tweet_id:
        tweet_id, digest_date = last_digest()
    if not tweet_id:
        log.error("No digest tweet id to measure")
        return 1

    data = fetch_metrics(tweet_id)
    if not data:
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
    code = run(tweet_id=tid, quiet=quiet)
    # The digest is the scheduled reason this runs; the replies ride along.
    # A digest that could not be measured must not stop them.
    code = measure_replies(quiet=quiet) or code
    sys.exit(code)
