"""Social-media KPIs for the Sunday stats run.

Five numbers, each read from the system that owns it:

    followers            X /2/users/me
    replies sent         our timeline, tweets that reply to someone else's
                         conversation — a self-thread does not count
    replies answered     of those, the ones that got a reply back
    top post             highest impression count in the window, from the
                         timeline, cross-checked against digest_metrics.jsonl
    social referrers     self-hosted Plausible, via ClickHouse
    registrations        the database, platform not in (ownify, test)

Called by scripts/daily_stats.sh on Sundays, and standalone any time:

    python scripts/sm_kpis.py               # measure and send
    python scripts/sm_kpis.py --quiet       # measure and print only
    python scripts/sm_kpis.py --days 30     # a different window
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import requests
from requests_oauthlib import OAuth1

from app import notify

DATA_DIR = os.path.expanduser("~/moltstack/data")
METRICS_FILE = os.path.join(DATA_DIR, "digest_metrics.jsonl")
KPI_FILE = os.path.join(DATA_DIR, "sm_kpis.jsonl")
DB_URL = os.environ.get("DATABASE_URL", "dbname=moltstack user=moltstack")
OUR_USER_ID = "2023702578836779008"  # @moltrust

# X Premium (the $8 tier) was activated on this date. Every row carries it so a
# later reader can tell which side of the change a number is on without having
# to remember. The API reports the live state too; this is the date, which the
# API does not give.
PREMIUM_SINCE = "2026-09-21"
CLICKHOUSE_CONTAINER = "plausible-plausible_events_db-1"
EXCLUDED_PLATFORMS = ("ownify", "test")

# Plausible's own source names. It classifies referrers itself; these are the
# ones that count as social for this report.
SOCIAL_SOURCES = {"twitter", "x", "linkedin", "bluesky", "mastodon", "reddit",
                  "hacker news", "facebook", "instagram", "threads", "youtube",
                  "telegram", "farcaster", "warpcast", "lobste.rs"}

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("sm_kpis")
notify.silence_http_request_logs()


def x_auth() -> OAuth1 | None:
    keys = [os.getenv(k, "") for k in
            ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    return OAuth1(*keys) if all(keys) else None


def followers(auth) -> int | None:
    try:
        r = requests.get("https://api.twitter.com/2/users/me",
                         params={"user.fields": "public_metrics"}, auth=auth, timeout=30)
        if r.status_code != 200:
            log.error(f"users/me {r.status_code}: {r.text[:200]}")
            return None
        return r.json()["data"]["public_metrics"]["followers_count"]
    except Exception as e:
        log.error(f"followers failed: {e}")
        return None


def subscription(auth) -> tuple[str | None, str | None]:
    """(subscription_type, verified_type) as X reports them right now."""
    try:
        r = requests.get("https://api.twitter.com/2/users/me",
                         params={"user.fields": "subscription_type,verified,verified_type"},
                         auth=auth, timeout=30)
        if r.status_code != 200:
            log.error(f"users/me {r.status_code}: {r.text[:200]}")
            return None, None
        d = r.json()["data"]
        return d.get("subscription_type"), d.get("verified_type")
    except Exception as e:
        log.error(f"subscription lookup failed: {e}")
        return None, None


def timeline(auth, since: datetime.datetime) -> list[dict]:
    """Our posts in the window, with enough fields to tell a reply from a thread."""
    out, token = [], None
    for _ in range(5):  # 500 posts is far more than a week of ours
        params = {"max_results": 100, "exclude": "retweets",
                  "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "tweet.fields": "created_at,public_metrics,referenced_tweets,"
                                  "conversation_id,in_reply_to_user_id,text"}
        if token:
            params["pagination_token"] = token
        try:
            r = requests.get(f"https://api.twitter.com/2/users/{OUR_USER_ID}/tweets",
                             params=params, auth=auth, timeout=30)
        except Exception as e:
            log.error(f"timeline failed: {e}")
            break
        if r.status_code != 200:
            log.error(f"timeline {r.status_code}: {r.text[:200]}")
            break
        body = r.json()
        out.extend(body.get("data", []))
        token = body.get("meta", {}).get("next_token")
        if not token:
            break
    return out


def reply_stats(tweets: list[dict]) -> tuple[int, int, int | None, str | None]:
    """(replies sent, replies answered, best impressions, best tweet id).

    A reply counts as sent when it answers someone else's conversation. Our own
    thread continuations reply to us, so they are excluded — otherwise every
    syndication thread would inflate the number by four.
    """
    sent, answered = 0, 0
    best_impr, best_id = None, None
    for t in tweets:
        refs = t.get("referenced_tweets") or []
        is_reply = any(r.get("type") == "replied_to" for r in refs)
        to_other = t.get("in_reply_to_user_id") not in (None, OUR_USER_ID)
        if is_reply and to_other:
            sent += 1
            if (t.get("public_metrics") or {}).get("reply_count", 0) > 0:
                answered += 1
        impr = (t.get("public_metrics") or {}).get("impression_count")
        if impr is not None and (best_impr is None or impr > best_impr):
            best_impr, best_id = impr, t.get("id")
    return sent, answered, best_impr, best_id


def top_from_metrics_file(since: datetime.datetime) -> tuple[int | None, str | None]:
    """Best measured digest in the window, from digest_metrics.jsonl."""
    best, best_id = None, None
    try:
        with open(METRICS_FILE) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                when = row.get("measured_at")
                if not when or datetime.datetime.fromisoformat(when) < since:
                    continue
                impr = row.get("impressions")
                if impr is not None and (best is None or impr > best):
                    best, best_id = impr, row.get("tweet_id")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"Cannot read {METRICS_FILE}: {e}")
    return best, best_id


def social_referrers(days: int) -> tuple[dict | None, int | None]:
    """(social sources with counts, total events) from the self-hosted Plausible.

    Read straight out of ClickHouse: the Plausible instance is bound to
    127.0.0.1 and has no API key issued, so its own stats API is not reachable
    from here without provisioning one.
    """
    # clickhouse-client takes bound parameters, so both statements are constants.
    query = ("SELECT referrer_source, count() FROM plausible_events_db.events_v2 "
             "WHERE timestamp > now() - INTERVAL {days:UInt16} DAY "
             "AND referrer_source != '' GROUP BY referrer_source")
    total_q = ("SELECT count() FROM plausible_events_db.events_v2 "
               "WHERE timestamp > now() - INTERVAL {days:UInt16} DAY")
    param = f"--param_days={int(days)}"
    try:
        out = subprocess.run(
            ["docker", "exec", "-i", CLICKHOUSE_CONTAINER, "clickhouse-client",
             param, "--query", query],
            capture_output=True, text=True, timeout=30)
        tot = subprocess.run(
            ["docker", "exec", "-i", CLICKHOUSE_CONTAINER, "clickhouse-client",
             param, "--query", total_q],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            log.error(f"clickhouse: {out.stderr[:200]}")
            return None, None
        social = {}
        for line in out.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                continue
            source, count = parts[0], int(parts[1])
            if source.strip().lower() in SOCIAL_SOURCES:
                social[source] = count
        total = int(tot.stdout.strip()) if tot.returncode == 0 and tot.stdout.strip() else None
        return social, total
    except Exception as e:
        log.error(f"plausible read failed: {e}")
        return None, None


def reply_decisions() -> dict | None:
    """The reply radar's tally: drafts sent, and what was decided about them.

    Read straight out of its state file rather than through the module, so a
    broken radar cannot take the Sunday stats down with it.
    """
    try:
        with open(os.path.join(DATA_DIR, "reply_radar_state.json")) as f:
            state = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning(f"Cannot read the radar state: {e}")
        return None
    decisions = state.get("decisions", {})
    post = sum(1 for d in decisions.values() if d.get("verb") == "post")
    drop = sum(1 for d in decisions.values() if d.get("verb") == "drop")
    # Mirrors reply_radar.decision_counts: drafts sent before the keyboard
    # existed were still decided on, so sent is never fewer than decided.
    sent = max(int(state.get("drafts_sent", 0)), post + drop)
    return {"sent": sent, "post": post, "drop": drop,
            "open": max(0, sent - post - drop)}


def registrations(days: int) -> tuple[int | None, int | None]:
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute(
            """SELECT count(*), count(DISTINCT platform) FROM agents
                WHERE created_at > now() - %s::interval
                  AND coalesce(platform,'') NOT IN %s""",
            (f"{int(days)} days", EXCLUDED_PLATFORMS))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0], row[1]
    except Exception as e:
        log.error(f"registrations failed: {e}")
        return None, None


def collect(days: int = 7) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=days)
    auth = x_auth()

    k: dict = {"measured_at": now.isoformat(), "window_days": days,
               "premium_since": PREMIUM_SINCE}
    if auth:
        k["followers"] = followers(auth)
        k["subscription"], k["verified_type"] = subscription(auth)
        tl = timeline(auth, since)
        k["posts"] = len(tl)
        sent, answered, best, best_id = reply_stats(tl)
        k["replies_sent"], k["replies_answered"] = sent, answered
        k["top_impressions"], k["top_tweet_id"] = best, best_id
    else:
        log.error("X credentials not available — X metrics skipped")
        k["x_error"] = "credentials missing"

    file_best, file_id = top_from_metrics_file(since)
    k["top_digest_impressions"], k["top_digest_tweet_id"] = file_best, file_id

    social, total = social_referrers(days)
    k["social_referrers"] = social
    k["plausible_events"] = total

    k["registrations"], k["registration_platforms"] = registrations(days)
    k["reply_decisions"] = reply_decisions()
    return k


def format_report(k: dict) -> str:
    d = k["window_days"]
    lines = [f"\U0001f4ca Social KPIs — last {d} days", ""]

    if "x_error" in k:
        lines.append(f"X: unavailable ({k['x_error']})")
    else:
        sub = k.get("subscription") or "none"
        lines.append(f"Followers: {k.get('followers')}  ·  {sub} "
                     f"(seit {k.get('premium_since')})")
        lines.append(f"Posts: {k.get('posts')}")
        lines.append(f"Replies sent: {k.get('replies_sent')} · "
                     f"answered: {k.get('replies_answered')}")
        if k.get("top_impressions") is not None:
            url = f"https://x.com/MolTrust/status/{k.get('top_tweet_id')}"
            lines.append(f"Top post: {k['top_impressions']} impressions — {url}")

    if k.get("top_digest_impressions") is not None:
        lines.append(f"Best measured digest: {k['top_digest_impressions']} impressions")

    social = k.get("social_referrers")
    if social is None:
        lines.append("Social referrers: Plausible unavailable")
    elif not social:
        lines.append(f"Social referrers: 0 of {k.get('plausible_events')} events")
    else:
        detail = ", ".join(f"{s} {n}" for s, n in
                           sorted(social.items(), key=lambda x: -x[1]))
        lines.append(f"Social referrers: {sum(social.values())} of "
                     f"{k.get('plausible_events')} events — {detail}")

    rd = k.get("reply_decisions")
    if rd is None:
        lines.append("Reply-Radar: noch keine Entwürfe")
    else:
        share = f"{100.0 * rd['post'] / (rd['post'] + rd['drop']):.0f} %" \
            if (rd["post"] + rd["drop"]) else "—"
        lines.append(f"Reply-Radar: {rd['sent']} gesendet · {rd['post']} Posten "
                     f"({share}) · {rd['drop']} Verwerfen · {rd['open']} offen")

    lines.append(f"Registrations: {k.get('registrations')} from "
                 f"{k.get('registration_platforms')} platforms "
                 f"(excluding {', '.join(EXCLUDED_PLATFORMS)})")
    return "\n".join(lines)


def run(days: int = 7, quiet: bool = False) -> int:
    k = collect(days)
    report = format_report(k)
    print(report)
    try:
        with open(KPI_FILE, "a") as f:
            f.write(json.dumps(k, sort_keys=True) + "\n")
        os.chmod(KPI_FILE, 0o640)
    except Exception as e:
        log.error(f"Cannot append to {KPI_FILE}: {e}")
    if not quiet:
        notify.send_telegram(report, channel=notify.STATS)
    return 0


if __name__ == "__main__":
    window = 7
    if "--days" in sys.argv:
        i = sys.argv.index("--days")
        if i + 1 < len(sys.argv):
            window = int(sys.argv[i + 1])
    sys.exit(run(days=window, quiet="--quiet" in sys.argv))
