"""Match Lars's posted replies to the drafts they answer (ops CM23).

Since CM16 a draft reaches Lars as an intent link and he posts it himself from
@LarsOnMoltrust. Nothing comes back to us from that, so the radar looks: it
reads his timeline with the read-only grant (app/x_lars_oauth2.py) and takes
every post whose `referenced_tweets` holds a `replied_to` id equal to the
target of a draft. Such a draft is closed as gesendet with route "link"
(agents/reply_decisions.close_posted) and the reply is booked for the CM5
measurement (scripts/lars_reply_metrics.record_linked), whose first booking
opens the window.

Lars 31.10.2026, CM23: "Kein Entwurf wird als verfallen gebucht, wenn eine
Antwort auf denselben Post von @LarsOnMoltrust vorliegt." The consumer runs
this before the twelve-hour sweep, so a reply posted in time closes its draft
before the sweep can expire it; a reply found only later still overrides
verfallen within reply_decisions.LINK_LOOKBACK_HOURS.

A reply counts only when it was written after the draft was offered — an
answer Lars gave to the same post before the radar drafted one is his own,
not the draft's.

Reads: GET /2/users/{id}/tweets (docs.x.com/x-api/users/get-posts, OAuth2
user token with tweet.read + users.read), at most every CHECK_MINUTES and only
while a draft can still be closed. Booked under x_meter.LARS_MATCH_SOURCE: a
read, in the CM5 column, never in the send cap. Without a grant this does
nothing and says so in the log; it never falls back to the @MolTrust tokens.
"""
from __future__ import annotations

import datetime
import json
import logging
import os

from app import paths, x_lars_oauth2
from agents import reply_decisions, x_meter

log = logging.getLogger("lars_reply_match")

USER_TWEETS_URL = "https://api.twitter.com/2/users/{id}/tweets"
ME_URL = "https://api.twitter.com/2/users/me"

# Nobody posts by hand in under a minute, and each look is a billed read.
CHECK_MINUTES = 30
# Three pages of a hundred cover three days of anyone's timeline.
MAX_PAGES = 3


def state_path() -> str:
    return paths.data("lars_reply_match_state.json")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parsed(stamp) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def _load() -> dict:
    try:
        with open(state_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    path = paths.ensure(state_path())
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, sort_keys=True)
    os.replace(tmp, path)


def _user_id(auth, state: dict) -> str | None:
    """Lars's numeric id, read once and kept; the handle is checked on that read."""
    if state.get("user_id"):
        return state["user_id"]
    r = x_meter.get(ME_URL, auth=auth, timeout=30, source=x_meter.LARS_MATCH_SOURCE)
    if r.status_code != 200:
        log.error(f"users/me answered {r.status_code}")
        return None
    d = (r.json() or {}).get("data") or {}
    if d.get("username") != x_lars_oauth2.EXPECTED_HANDLE:
        log.error(f"grant belongs to @{d.get('username')}, not "
                  f"@{x_lars_oauth2.EXPECTED_HANDLE}; nothing read")
        return None
    state["user_id"] = d.get("id")
    return state["user_id"]


def _replies_since(auth, user_id: str, start: datetime.datetime) -> list[dict] | None:
    """Lars's posts since `start`, replies included. None when X did not answer."""
    out: list[dict] = []
    params = {"max_results": 100, "exclude": "retweets",
              "tweet.fields": "referenced_tweets,created_at",
              "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ")}
    for _ in range(MAX_PAGES):
        r = x_meter.get(USER_TWEETS_URL.format(id=user_id), params=params, auth=auth,
                        timeout=30, source=x_meter.LARS_MATCH_SOURCE)
        if r.status_code != 200:
            log.error(f"X API {r.status_code} on Lars's timeline")
            return None
        body = r.json() or {}
        out.extend(body.get("data") or [])
        nxt = (body.get("meta") or {}).get("next_token")
        if not nxt:
            break
        params = {**params, "pagination_token": nxt}
    return out


def run(now: datetime.datetime | None = None, force: bool = False) -> dict:
    """One look at Lars's timeline. Returns what happened, for the log and tests."""
    now = now or _now()
    candidates = reply_decisions.link_candidates(now)
    if not candidates:
        return {"skipped": "no draft to match"}
    state = _load()
    last = _parsed(state.get("checked_at"))
    if not force and last and now - last < datetime.timedelta(minutes=CHECK_MINUTES):
        return {"skipped": "checked recently"}
    state["checked_at"] = now.isoformat()

    auth = x_lars_oauth2.auth(now)
    if auth is None:
        _save(state)
        log.info("no read grant for @LarsOnMoltrust; posted replies not matched")
        return {"skipped": "no grant"}
    try:
        user_id = _user_id(auth, state)
        offered = {tid: _parsed(at) for tid, at in candidates.items()}
        start = min(t for t in offered.values() if t)
        posts = _replies_since(auth, user_id, start) if user_id else None
    except Exception as e:
        _save(state)
        log.error(f"Lars timeline read failed: {type(e).__name__}")
        return {"skipped": "read failed"}
    _save(state)
    if posts is None:
        return {"skipped": "read failed"}

    from scripts import lars_reply_metrics
    done: dict[str, str] = {}
    # Oldest first, so the first reply to a target is the one that counts.
    for t in sorted(posts, key=lambda p: p.get("created_at") or ""):
        created = _parsed(t.get("created_at"))
        for ref in t.get("referenced_tweets") or []:
            target = str(ref.get("id") or "")
            if ref.get("type") != "replied_to" or target not in offered or target in done:
                continue
            if created is None or (offered[target] and created < offered[target]):
                continue
            link = f"https://x.com/{x_lars_oauth2.EXPECTED_HANDLE}/status/{t['id']}"
            result = reply_decisions.close_posted(target, t["id"], link,
                                                  t.get("created_at"), now=now)
            if not result:
                continue
            done[target] = result
            try:
                lars_reply_metrics.record_linked(t["id"], target, t.get("created_at"))
            except Exception as e:
                log.error(f"CM5 booking of {t['id']} failed: {type(e).__name__}")
            log.info(f"  {target}: posted by @{x_lars_oauth2.EXPECTED_HANDLE} "
                     f"({result}) {link}")
    return {"matched": done, "read": len(posts)}
