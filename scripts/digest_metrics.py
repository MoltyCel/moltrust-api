"""Measure the daily digest six hours after it posts.

Cron: 18:00 UTC daily, six hours after the 12:00 digest. Reads the tweet id
herald_v3 stored, asks X for its public metrics, appends one line to
data/digest_metrics.jsonl and sends the figures to Telegram.

The file is the point. Impressions are only worth anything as a series, and a
number that lives in a chat message is gone by the following week; sm_kpis.py
reads this file for the weekly top-post figure.

    python scripts/digest_metrics.py              # the scheduled run
    python scripts/digest_metrics.py --quiet      # no Telegram, still writes
    python scripts/digest_metrics.py --tweet-id N # measure one specific post
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

# A reply is worth measuring for as long as it can still move; after that the
# row would repeat itself daily for nothing.
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


def followers_now(auth) -> int | None:
    try:
        r = requests.get("https://api.twitter.com/2/users/me",
                         params={"user.fields": "public_metrics"}, auth=auth, timeout=30)
        if r.status_code == 200:
            return r.json()["data"]["public_metrics"]["followers_count"]
    except Exception as e:
        log.warning(f"follower lookup failed: {e}")
    return None


def posted_replies() -> list[dict]:
    """Replies the radar actually sent, newest first."""
    try:
        with open(RADAR_STATE) as f:
            decisions = json.load(f).get("decisions", {})
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning(f"Cannot read the radar state: {e}")
        return []
    out = []
    for target_id, d in decisions.items():
        if d.get("result") != "posted" or not d.get("reply_id"):
            continue
        out.append({"target_id": target_id, **d})
    return sorted(out, key=lambda d: d.get("posted_at") or "", reverse=True)


def measure_replies(auth, now: datetime.datetime, quiet: bool) -> list[str]:
    """One row per live reply: its own metrics, its target, the follower delta."""
    replies = posted_replies()
    if not replies:
        return []
    followers = followers_now(auth)
    lines = []
    for r in replies:
        posted_at = r.get("posted_at")
        if posted_at:
            age_days = (now - datetime.datetime.fromisoformat(posted_at)).days
            if age_days > REPLY_TRACK_DAYS:
                continue
        data = fetch_metrics(r["reply_id"])
        if not data:
            continue
        pm = data.get("public_metrics", {})
        target = fetch_metrics(r["target_id"]) or {}
        tpm = target.get("public_metrics", {})
        base = r.get("followers_at_post")
        delta = (followers - base) if (followers is not None and base is not None) else None
        row = {
            "measured_at": now.isoformat(),
            "kind": "reply",
            "reply_id": r["reply_id"],
            "target_id": r["target_id"],
            "posted_at": posted_at,
            "impressions": pm.get("impression_count"),
            "likes": pm.get("like_count"),
            "replies": pm.get("reply_count"),
            "target_impressions": tpm.get("impression_count"),
            "followers": followers,
            "followers_at_post": base,
            "followers_delta": delta,
        }
        append(row)
        log.info(json.dumps(row))
        lines.append(f"↪ {r['reply_id']}: {row['impressions']} impressions · "
                     f"{row['likes']} likes · Ziel {row['target_impressions']} · "
                     f"Follower {'+' if (delta or 0) >= 0 else ''}{delta if delta is not None else '?'}\n"
                     f"  {r.get('link')}")
    return lines


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
        "measured_at": now.isoformat(),
        "kind": "digest",
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
    reply_lines = measure_replies(x_auth(), now, quiet)
    if reply_lines:
        msg += "\n\nReplies:\n" + "\n".join(reply_lines)
    print(msg)
    if not quiet:
        notify.send_telegram(msg, channel=notify.STATS)
    return 0


if __name__ == "__main__":
    tid = None
    if "--tweet-id" in sys.argv:
        i = sys.argv.index("--tweet-id")
        tid = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    sys.exit(run(tweet_id=tid, quiet="--quiet" in sys.argv))
