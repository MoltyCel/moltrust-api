"""Measure the replies Lars sends from his own X account (ops CM5).

The rules this file carries out are in docs/reply-radar-lars.md and were fixed
before the first reply went out. In short: an eight-week window from the first
reply sent through the radar (CM3), an interim report after four weeks, and
one threshold — median >= 30 impressions per reply and >= 1 profile click per
2 replies, the same bar the @MolTrust reply branch was judged by.

From 31.10.2026 (ops CM22/CM23) nothing is sent through CM3 any more: Lars
posts each draft himself through the intent link (CM16), and
agents/lars_reply_match.py finds the reply on his timeline and books it here
with route "link". The first such reply opens the window exactly as a reply
sent through CM3 would have. The reads are signed with a read-only OAuth 2.0
user token (app/x_lars_oauth2.py: tweet.read users.read offline.access, no
tweet.write); X serves non_public_metrics to "OAuth 1.0a or OAuth 2.0 with
user context for posts you own" (docs.x.com/x-api/fundamentals/metrics).

Why a file of its own and not a third series in digest_metrics.py: X serves
profile clicks only to the author, so these reads are signed with Lars' tokens,
not the @MolTrust ones. A run that mixed both would either measure Lars' replies
with the wrong account (no clicks) or put his rows into the @MolTrust series.
Kept apart, in three files:

  data/reply_radar_lars.jsonl          the replies: one line per sent or copied reply
  data/reply_radar_lars_metrics.jsonl  the measurement: one line per reply and day,
                                       one line per day for the follower count
  data/reply_radar_lars_state.json     which of the two reports has gone out

Cost and the caps. Every read here goes through x_meter.get with the source
LEDGER_SOURCE, so its cost stands in its own column (CM9). It reads only, so it
can never count against the weekly send cap (CM4), and it consults neither
that cap nor the read breaker: a cost pause must not cut the measurement off
(Lars 22.10.2026, CM4 criterion 5, CM5 criterion 3).

    python scripts/lars_reply_metrics.py                 # the daily run
    python scripts/lars_reply_metrics.py --quiet         # files no report, still writes

The two reports go into the inbox of the daily report (app/tagesbericht_eingang)
and reach Telegram only inside the selftest digest, never as a message of their
own (CM5 criterion 5 as of 23.10.2026, ops NOTIFY-3b).
    python scripts/lars_reply_metrics.py --add-copied URL [--target ID]
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify, paths, tagesbericht_eingang, x_lars_oauth2
from agents import reply_decisions, x_meter

log = logging.getLogger("lars_reply_metrics")

TWEETS_URL = "https://api.twitter.com/2/tweets"
ME_URL = "https://api.twitter.com/2/users/me"
USER_TWEETS_URL = "https://api.twitter.com/2/users/{id}/tweets"

# The token names Lars' account is unlocked under (CM3). The consumer pair is
# the existing app's; only the access pair is his. There is deliberately no
# fallback to X_ACCESS_TOKEN: that pair is @MolTrust, and a read signed with it
# returns no profile clicks for Lars' replies — a zero that looks like a result.
LARS_TOKEN_VARS = ("X_CONSUMER_KEY", "X_CONSUMER_SECRET",
                   "X_LARS_ACCESS_TOKEN", "X_LARS_ACCESS_SECRET")

LEDGER_SOURCE = "lars-reply-metrics"
# The name under which the two reports stand in the daily report.
REPORT_SOURCE = "lars_reply_metrics"

# Lars 22.10.2026, CM5 criterion 2.
WINDOW_DAYS = 56
INTERIM_DAYS = 28
THRESHOLD_MEDIAN_IMPRESSIONS = 30
THRESHOLD_CLICKS_PER_REPLY = 0.5

# How long one reply is measured daily. The same two weeks as the @MolTrust
# series (digest_metrics.REPLY_TRACK_DAYS): impressions on a reply are done
# inside a week, and re-reading every reply of eight weeks each day would bill
# up to a hundred posts a day for numbers that no longer move.
REPLY_TRACK_DAYS = 14

ROUTE_SENT = "api"       # sent with the Send button, through CM3
ROUTE_LINK = "link"      # posted through the intent link, found on his timeline (CM23)
ROUTE_COPIED = "copied"  # posted by hand, counted only once Lars reports the URL
# The routes that open the window. CM22 criterion 5: a reply found through the
# link counts like one sent through CM3; a copied one still does not.
WINDOW_ROUTES = (ROUTE_SENT, ROUTE_LINK)

_STATUS_RE = re.compile(r"/status(?:es)?/(\d+)")


def replies_path() -> str:
    return paths.data("reply_radar_lars.jsonl")


def metrics_path() -> str:
    return paths.data("reply_radar_lars_metrics.jsonl")


def state_path() -> str:
    return paths.data("reply_radar_lars_state.json")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parsed(stamp: str | None) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def _append(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(path, 0o640)


def _read_jsonl(path: str) -> list[dict]:
    rows = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return rows


# ── the replies ─────────────────────────────────────────────────────────────

def record_sent(reply_id: str, target_id: str, posted_at: str | None = None) -> None:
    """Book one reply sent through the radar from Lars' account.

    The call CM3 makes after X has accepted the post. The first of these opens
    the measurement window.
    """
    _append(replies_path(), {
        "reply_id": str(reply_id), "target_id": str(target_id),
        "route": ROUTE_SENT, "posted_at": posted_at or _now().isoformat(),
        "booked_at": _now().isoformat()})


def record_linked(reply_id: str, target_id: str, posted_at: str | None) -> bool:
    """Book a reply found on Lars's timeline for a draft's target (CM23).

    Once per reply id; False when it is already booked under any route.
    """
    reply_id = str(reply_id)
    if any(r.get("reply_id") == reply_id for r in _read_jsonl(replies_path())):
        return False
    _append(replies_path(), {
        "reply_id": reply_id, "target_id": str(target_id),
        "route": ROUTE_LINK, "posted_at": posted_at or _now().isoformat(),
        "booked_at": _now().isoformat()})
    return True


def record_copied(url: str, target_id: str | None = None) -> str:
    """Book a reply Lars posted by hand and reported by URL (CM5 criterion 4).

    Only a reported URL counts: a copied text that nobody reports is not in the
    series. The time it went out is not known here; the measurement takes it
    from X (`created_at`). Returns the reply id.
    """
    m = _STATUS_RE.search(url or "")
    if not m:
        raise ValueError(f"no status id in {url!r}")
    reply_id = m.group(1)
    if any(r.get("reply_id") == reply_id for r in _read_jsonl(replies_path())):
        raise ValueError(f"reply {reply_id} is already booked")
    _append(replies_path(), {
        "reply_id": reply_id, "target_id": str(target_id) if target_id else None,
        "route": ROUTE_COPIED, "posted_at": None, "url": url,
        "booked_at": _now().isoformat()})
    return reply_id


def replies() -> list[dict]:
    return _read_jsonl(replies_path())


def window_start(rows: list[dict] | None = None) -> datetime.datetime | None:
    """The first reply sent through CM3 or posted through the link (CM22).

    A copied reply does not open the window.
    """
    sent = [_parsed(r.get("posted_at")) for r in (rows if rows is not None else replies())
            if r.get("route") in WINDOW_ROUTES]
    sent = [s for s in sent if s]
    return min(sent) if sent else None


# ── X ───────────────────────────────────────────────────────────────────────

def lars_auth():
    """Lars's read-only OAuth 2.0 grant (CM22), refreshed when due.

    The OAuth 1.0a pair from CM3 is still honoured when present, but it was
    never issued and CM3 has fallen away. Neither path ever reads the
    @MolTrust pair.
    """
    bearer = x_lars_oauth2.auth()
    if bearer is not None:
        return bearer
    keys = [os.getenv(k, "") for k in LARS_TOKEN_VARS]
    if not all(keys):
        return None
    from requests_oauthlib import OAuth1
    return OAuth1(*keys)


def me(auth) -> dict | None:
    """Lars' user id, handle and follower count, in one billed profile read."""
    try:
        r = x_meter.get(ME_URL, params={"user.fields": "public_metrics"},
                        auth=auth, timeout=30, source=LEDGER_SOURCE)
    except Exception as e:
        log.error(f"X request failed: {type(e).__name__}")
        return None
    if r.status_code != 200:
        log.error(f"X API {r.status_code} on users/me")
        return None
    d = r.json().get("data") or {}
    return {"id": d.get("id"), "username": d.get("username"),
            "followers": (d.get("public_metrics") or {}).get("followers_count")}


def own_posts_between(auth, user_id: str, start: datetime.datetime,
                      end: datetime.datetime) -> int | None:
    """Lars' own posts in [start, end): no replies, no reposts.

    A day on which he posted something of his own cannot attribute its new
    followers to the replies; this is the count that decides it. None when X
    did not answer — unknown is not zero.
    """
    try:
        r = x_meter.get(USER_TWEETS_URL.format(id=user_id), auth=auth, timeout=30,
                        source=LEDGER_SOURCE,
                        params={"exclude": "replies,retweets", "max_results": 100,
                                "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
    except Exception as e:
        log.error(f"X request failed: {type(e).__name__}")
        return None
    if r.status_code != 200:
        log.error(f"X API {r.status_code} on the own timeline")
        return None
    body = r.json()
    if (body.get("meta") or {}).get("next_token"):
        # More than a hundred own posts in a day is not a gap to paper over.
        log.error("own timeline has a further page; count not complete")
        return None
    return len(body.get("data") or [])


def fetch_replies(auth, ids: list[str]) -> dict[str, dict] | None:
    """Metrics for up to 100 replies, keyed by id. None when X did not answer."""
    if not ids:
        return {}
    try:
        r = x_meter.get(TWEETS_URL, auth=auth, timeout=30, source=LEDGER_SOURCE,
                        params={"ids": ",".join(ids[:100]),
                                # non_public_metrics carries user_profile_clicks,
                                # and X serves it to the author only.
                                "tweet.fields": "created_at,author_id,public_metrics,"
                                                "non_public_metrics"})
    except Exception as e:
        log.error(f"X request failed: {type(e).__name__}")
        return None
    if r.status_code != 200:
        log.error(f"X API {r.status_code} on tweets")
        return None
    return {d["id"]: d for d in r.json().get("data") or []}


# ── the daily run ───────────────────────────────────────────────────────────

def tracked(rows: list[dict], now: datetime.datetime) -> list[dict]:
    """Replies still inside their two weeks of daily measurement.

    A copied reply is booked without a time; after its first measurement the
    time X reported is in the metrics file, and that one counts.
    """
    seen = {m["tweet_id"]: m.get("posted_at") for m in _read_jsonl(metrics_path())
            if m.get("kind") == "reply" and m.get("posted_at")}
    out = []
    for r in rows:
        posted = _parsed(r.get("posted_at") or seen.get(r["reply_id"]))
        if posted and (now - posted).days > REPLY_TRACK_DAYS:
            continue
        out.append(r)
    return out


def measure(now: datetime.datetime | None = None, quiet: bool = False) -> int:
    now = now or _now()
    rows = replies()
    start = window_start(rows)
    if start is None:
        log.info("window not started: no posted reply from Lars yet")
        return 0
    state = load_state()
    if state.get("final_sent_at"):
        log.info(f"window closed: final report sent {state['final_sent_at']}")
        return 0

    auth = lars_auth()
    if auth is None:
        log.error("window open but Lars' X grant is missing "
                  f"({x_lars_oauth2.ACCESS_VAR}); nothing measured")
        return 1

    account = me(auth)
    if not account or not account.get("id"):
        log.error("users/me did not answer; nothing measured")
        return 1

    code = 0
    code = max(code, write_follower_row(auth, account, now))
    code = max(code, write_reply_rows(auth, account, tracked(rows, now), now))
    code = max(code, report_if_due(start, now, quiet, state))
    return code


def write_follower_row(auth, account: dict, now: datetime.datetime) -> int:
    """One row per day: followers now, and whether the span had an own post."""
    prev = [r for r in _read_jsonl(metrics_path()) if r.get("kind") == "followers"]
    prev_row = prev[-1] if prev else None
    since = _parsed(prev_row.get("measured_at")) if prev_row else None
    since = since or now - datetime.timedelta(days=1)
    own = own_posts_between(auth, account["id"], since, now)
    followers = account.get("followers")
    before = prev_row.get("followers") if prev_row else None
    row = {
        "kind": "followers", "measured_at": now.isoformat(),
        "since": since.isoformat(), "followers": followers,
        "new_followers": (None if followers is None or before is None
                          else followers - before),
        "own_posts": own,
        # Only a span without an own post says anything about the replies.
        # Unknown (own is None) is not counted as such a span.
        "counts": own == 0,
    }
    _append(metrics_path(), row)
    log.info(json.dumps(row))
    return 0 if own is not None else 1


def write_reply_rows(auth, account: dict, rows: list[dict],
                     now: datetime.datetime) -> int:
    if not rows:
        return 0
    got = fetch_replies(auth, [r["reply_id"] for r in rows])
    if got is None:
        return 1
    for r in rows:
        d = got.get(r["reply_id"])
        if not d:
            log.warning(f"reply {r['reply_id']} not readable — skipped")
            continue
        if d.get("author_id") and d["author_id"] != account["id"]:
            # A reported URL that is not Lars' post: its clicks are not ours to
            # read, and it is not his reply.
            log.warning(f"reply {r['reply_id']} is not from Lars' account — skipped")
            continue
        pm = d.get("public_metrics") or {}
        npm = d.get("non_public_metrics") or {}
        posted = _parsed(d.get("created_at"))
        row = {
            "kind": "reply", "measured_at": now.isoformat(),
            "tweet_id": r["reply_id"], "target_id": r.get("target_id"),
            "route": r.get("route"), "copied": r.get("route") == ROUTE_COPIED,
            "posted_at": d.get("created_at"),
            "age_hours": (round((now - posted).total_seconds() / 3600, 1)
                          if posted else None),
            "impressions": pm.get("impression_count"),
            "likes": pm.get("like_count"),
            "replies": pm.get("reply_count"),
            "profile_clicks": npm.get("user_profile_clicks"),
            "engagements": npm.get("engagements"),
        }
        _append(metrics_path(), row)
        log.info(json.dumps(row))
    return 0


# ── the two reports ─────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(state_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, sort_keys=True)
    os.replace(tmp, state_path())


def summary(start: datetime.datetime, until: datetime.datetime) -> dict:
    """The figures for replies posted in [start, until), newest row per reply."""
    latest: dict[str, dict] = {}
    follower_rows = []
    for row in _read_jsonl(metrics_path()):
        if row.get("kind") == "followers":
            when = _parsed(row.get("measured_at"))
            if when and start <= when <= until + datetime.timedelta(days=1):
                follower_rows.append(row)
            continue
        if row.get("kind") != "reply":
            continue
        posted = _parsed(row.get("posted_at"))
        if not posted or not (start <= posted < until):
            continue
        prev = latest.get(row["tweet_id"])
        if not prev or (_parsed(prev["measured_at"]) or start) < (
                _parsed(row["measured_at"]) or start):
            latest[row["tweet_id"]] = row
    rs = list(latest.values())
    impr = [r["impressions"] for r in rs if r.get("impressions") is not None]
    clicks = [r["profile_clicks"] for r in rs if r.get("profile_clicks") is not None]
    median = statistics.median(impr) if impr else None
    per_reply = round(sum(clicks) / len(rs), 2) if rs and clicks else None
    quiet_days = [r for r in follower_rows if r.get("counts")
                  and r.get("new_followers") is not None]
    return {
        "replies": len(rs),
        "sent": sum(1 for r in rs if not r.get("copied")),
        "copied": sum(1 for r in rs if r.get("copied")),
        "median_impressions": median,
        "clicks_total": sum(clicks) if clicks else None,
        "clicks_per_reply": per_reply,
        "passes_median": median is not None and median >= THRESHOLD_MEDIAN_IMPRESSIONS,
        "passes_clicks": per_reply is not None and per_reply >= THRESHOLD_CLICKS_PER_REPLY,
        "quiet_days": len(quiet_days),
        "quiet_day_new_followers": sum(r["new_followers"] for r in quiet_days),
        # CM6 criterion 4: every draft's outcome in the same window, and the
        # reasons behind the discards by frequency — whether the drafts were
        # weak or the channel was, read side by side with the figures above.
        "decisions": reply_decisions.summary(start, until),
    }


def format_report(label: str, start: datetime.datetime, until: datetime.datetime,
                  s: dict) -> str:
    def mark(ok: bool) -> str:
        return "erreicht" if ok else "nicht erreicht"
    lines = [
        f"\U0001f4cf Replies Lars, {label} ({start:%d.%m.} bis {until:%d.%m.%Y})",
        "",
        f"Antworten: {s['replies']} (gesendet {s['sent']}, kopiert {s['copied']})",
        f"Median Aufrufe je Antwort: {s['median_impressions']} "
        f"(Schwelle ≥ {THRESHOLD_MEDIAN_IMPRESSIONS}, {mark(s['passes_median'])})",
        f"Profilklicks je Antwort: {s['clicks_per_reply']} "
        f"(Schwelle ≥ {THRESHOLD_CLICKS_PER_REPLY}, {mark(s['passes_clicks'])})",
        f"Tage ohne eigenen Post: {s['quiet_days']}, "
        f"neue Follower an diesen Tagen: {s['quiet_day_new_followers']}",
        *(reply_decisions.format_summary(s["decisions"]) if s.get("decisions") else []),
        "",
        ("Beide Schwellen erreicht." if s["passes_median"] and s["passes_clicks"]
         else "Schwelle nicht erreicht."),
    ]
    return "\n".join(lines)


def report_if_due(start: datetime.datetime, now: datetime.datetime, quiet: bool,
                  state: dict | None = None) -> int:
    """File the interim report at four weeks and the final one at eight, once each.

    Filed means: in the inbox of the daily report, which the selftest digest
    reads (ops NOTIFY-3b). interim_sent_at and final_sent_at are set when the
    report is filed; the digest owns delivery from there and keeps the inbox
    until Telegram accepted it.
    """
    state = dict(state if state is not None else load_state())
    for key, days, label in (("interim_sent_at", INTERIM_DAYS, "Zwischenstand nach vier Wochen"),
                             ("final_sent_at", WINDOW_DAYS, "Endstand nach acht Wochen")):
        until = start + datetime.timedelta(days=days)
        if state.get(key) or now < until:
            continue
        text = format_report(label, start, until, summary(start, until))
        print(text)
        if quiet:
            continue
        if tagesbericht_eingang.ablegen(REPORT_SOURCE, text):
            state[key] = now.isoformat()
            save_state(state)
        else:
            # Not marked as sent, so the next daily run tries again.
            log.error(f"{key[:-8]} report not filed for the daily report")
            return 1
    return 0


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] %(levelname)s: %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    notify.silence_http_request_logs()
    if "--add-copied" in argv:
        i = argv.index("--add-copied")
        target = None
        if "--target" in argv:
            target = argv[argv.index("--target") + 1]
        try:
            rid = record_copied(argv[i + 1], target)
        except (IndexError, ValueError) as e:
            log.error(str(e))
            return 2
        log.info(f"copied reply {rid} booked")
        return 0
    return measure(quiet="--quiet" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
