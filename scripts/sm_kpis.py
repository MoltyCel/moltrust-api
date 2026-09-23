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
    Moltbook spam        share of our own comments Moltbook marks as spam,
                         per identity, from its comments endpoint

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

# Moltbook marks comments it considers spam, and it does so against us: on
# 2026-09-22, 91 of u/moltrust-agent's last 100 comments carried is_spam, every
# one of them scored 0, and none had been answered. The agent-to-agent offer to
# the 43 dialogue partners waits on this share falling under the threshold
# below, so it is a gate, not decoration.
MOLTBOOK_API = "https://www.moltbook.com/api/v1"
MOLTBOOK_IDENTITIES = (("u/moltrust-agent", "MOLTBOOK_AGENT_KEY"),
                       ("u/moltguard_v1", "MOLTGUARD_MOLTBOOK_KEY"))
SPAM_THRESHOLD_PCT = 30

# What the endpoint actually does, measured 2026-09-23 rather than assumed:
#
#   * `limit` is capped at 100. limit=200 returns 100 rows, no error.
#   * `cursor`, fed the `next_cursor` of the previous page, does page. The
#     other spellings (after, before, offset, page) are accepted and ignored,
#     which is how a single page can look like the whole history. The check
#     that settles it is whether the first row moves: with the cursor the page
#     started at 2026-09-21T03:15:45Z instead of 2026-09-23T02:45:54Z.
#
# So this reads the window rather than one page. That matters for what the
# number means: the last 100 comments of a busy agent span two days, and once
# the heartbeat stops commenting they will span weeks and keep reporting the
# old advertising comments long after the change took effect — a gate that
# would never open. A windowed read reports the week that was asked about.
#
# The page ceiling is a refusal, not a truncation (CLAUDE.md, "Vollständigkeit
# beim Lesen"). Reaching it means the window was not fully read, and the report
# then says sample and names its size instead of calling it a weekly figure.
MOLTBOOK_PAGE_LIMIT = 100
MOLTBOOK_MAX_PAGES = 20

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
                # Reply rows share the file since 2026-09-22 and are a
                # different question; rows older than that carry no `kind`.
                if row.get("kind", "digest") != "digest":
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


def _moltbook_comments(key: str, since: datetime.datetime) -> tuple[list[dict], bool]:
    """Our comments back to `since`, and whether the read reached that far.

    False means the page ceiling or a broken page stopped the read short, and
    the caller must not present what came back as a figure for the window.
    """
    rows: list[dict] = []
    cursor, seen_first = None, set()
    for _ in range(MOLTBOOK_MAX_PAGES):
        params = {"limit": MOLTBOOK_PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(f"{MOLTBOOK_API}/agents/me/comments", params=params,
                             headers={"Authorization": f"Bearer {key}"}, timeout=30)
        except Exception as e:
            log.error(f"moltbook comments failed: {e}")
            return rows, False
        if r.status_code != 200:
            log.error(f"moltbook comments {r.status_code}: {r.text[:200]}")
            return rows, False
        body = r.json()
        page = body.get("comments") or []
        if not page:
            return rows, True
        # A cursor the server ignores returns the same page forever. Without
        # this the loop would read the newest 100 comments twenty times and
        # report the total as two thousand.
        first = page[0].get("id")
        if first in seen_first:
            log.error("moltbook cursor did not advance — stopping short")
            return rows, False
        seen_first.add(first)
        rows.extend(page)
        oldest = page[-1].get("created_at") or ""
        if oldest and _parsed(oldest) is not None and _parsed(oldest) < since:
            return rows, True
        if not body.get("has_more"):
            return rows, True
        cursor = body.get("next_cursor")
        if not cursor:
            return rows, True
    log.error(f"moltbook comments: {MOLTBOOK_MAX_PAGES} pages read and the "
              f"window is still not covered")
    return rows, False


def _parsed(ts: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def moltbook_spam(days: int) -> dict[str, dict]:
    """Per identity: how much of what we wrote on Moltbook is marked as spam."""
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    out: dict[str, dict] = {}
    for name, env_var in MOLTBOOK_IDENTITIES:
        key = os.getenv(env_var, "")
        if not key:
            log.error(f"{env_var} not in env — {name} skipped")
            out[name] = {"error": f"{env_var} missing"}
            continue
        rows, covered = _moltbook_comments(key, since)
        # Nothing read and the read broke is an outage. Nothing read and the
        # read finished is an agent that has not commented, which the report
        # says in words instead of calling it a rate.
        if not rows and not covered:
            out[name] = {"error": "read failed"}
            continue
        # A covered read counts the window and reports a week. A short one
        # counts everything it got and reports a sample of that size — the two
        # are different statements and the report keeps them apart.
        if covered:
            scope = [c for c in rows
                     if (_parsed(c.get("created_at") or "") or since) >= since]
        else:
            scope = rows
        spam = sum(1 for c in scope if c.get("is_spam"))
        out[name] = {"comments": len(scope), "spam": spam,
                     "pct": round(100.0 * spam / len(scope), 1) if scope else None,
                     "window_covered": covered, "window_days": days}
    return out


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
    k["moltbook_spam"] = moltbook_spam(days)
    return k


def spam_lines(spam: dict) -> list[str]:
    """The Moltbook-spam block of the report.

    Its own function so tests/test_sm_kpis_moltbook_spam.py can lift it out of
    this file and run it without psycopg2, requests_oauthlib and app.notify,
    which the rest of the module imports at module scope.

    The threshold stands in the heading because the number exists for it: the
    agent-to-agent offer to the 43 dialogue partners goes out once the share is
    under it. A line carrying only a percentage gets read as weather.
    """
    lines = [f"Moltbook-Spam (A2A-Angebot erst unter {SPAM_THRESHOLD_PCT} %):"]
    if not spam:
        lines.append("  nicht gemessen")
    for name, row in spam.items():
        if row.get("error"):
            lines.append(f"  {name}: nicht ermittelbar ({row['error']})")
            continue
        n, s, pct = row["comments"], row["spam"], row["pct"]
        d = row["window_days"]
        if not n:
            # An identity that stopped commenting has nothing in the window,
            # which is what success looks like here. A share of no comments is
            # not 0 %, and printing one would read as a cleared gate.
            lines.append(f"  {name}: keine Kommentare in den letzten {d} Tagen")
            continue
        # A rate out of seven comments is not a rate. The sample size travels
        # with the number so nobody opens the gate on four of five.
        small = "  (Stichprobe klein)" if n < 20 else ""
        if row["window_covered"]:
            lines.append(f"  {name}: {pct:g} %, {s} von {n} Kommentaren der "
                         f"letzten {d} Tage{small}")
        else:
            # Said in full every week on purpose. The short form travels, gets
            # pasted into a decision, and the qualifier stays behind.
            lines.append(f"  {name}: {pct:g} %, {s} von {n} gelesenen Kommentaren. "
                         f"Die {d} Tage wurden nicht vollstaendig gelesen, also "
                         f"eine Stichprobe und kein Wochenwert.{small}")
    return lines


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

    lines.extend(spam_lines(k.get("moltbook_spam") or {}))
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
