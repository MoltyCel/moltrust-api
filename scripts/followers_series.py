"""The X follower count once an hour, and who arrived when it went up.

Until 2026-10-12 the only follower series was the 18:00 reading in
digest_metrics.jsonl: one number a day, net. A follower who arrived at 07:10,
forty minutes after a reply, and one who arrived at 17:50 looked the same, and
"does a new follower follow a reply in time" could only be answered to the day.

    python scripts/followers_series.py          # the hourly cron run

Each run appends one row to data/followers.jsonl:

    {"at": ..., "hour": "2026-10-13T07:00Z", "followers": 37,
     "listed": true, "new_ids": ["1234"]}

**What it costs.** One /2/users/me read, $0.010 — and X deduplicates a resource
within a UTC day, so the same profile read 24 times is billed once. The
follower list is read only when the count went up, newest first, with
max_results sized to the increase plus a margin: a few profiles at $0.010 each.
The first run reads the whole list once to know who was already there.

**What it does not say.** The count is net: one follow and one unfollow in the
same hour leave it unchanged and trigger no list read. The margin catches a
hidden pair only when the count did move. Ids only — no names or handles are
written, and they are not needed to line an arrival up against a post.

**Timing, not attribution.** A follower first seen at 08:00 after a reply at
07:30 followed after the reply. Whether because of it is not in the data.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from requests_oauthlib import OAuth1

from agents import x_meter

ME_URL = "https://api.twitter.com/2/users/me"
FOLLOWERS_URL = "https://api.twitter.com/2/users/{id}/followers"

# Profiles read beyond the increase, for an unfollow hidden behind a follow in
# the same hour. Five profiles is $0.05 at worst.
LIST_MARGIN = 4
# X takes up to 1000 per page; 100 keeps one page well inside a day's profile
# budget. The baseline pages on, and stops with an error at this many pages
# rather than call a partial list the list.
PAGE_SIZE = 100
MAX_PAGES = 20

log = logging.getLogger("followers_series")


def series_path() -> str:
    from app import paths
    return paths.data("followers.jsonl")


def x_auth() -> OAuth1 | None:
    keys = [os.getenv(k, "") for k in
            ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    return OAuth1(*keys) if all(keys) else None


def read_series(path: str | None = None) -> list[dict]:
    out = []
    try:
        with open(path or series_path()) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return []
    return out


def known_ids(rows: list[dict]) -> set[str]:
    ids: set[str] = set()
    for r in rows:
        ids.update(r.get("baseline_ids") or [])
        ids.update(r.get("new_ids") or [])
    return ids


def needs_list(rows: list[dict], followers: int) -> bool:
    """Whether this hour's count calls for a list read.

    Up against the previous row is the ordinary case. Up against the last row
    whose list was actually read covers the hour that went up while reading
    was paused: the count the next hour is equal, not higher, and the arrival
    would otherwise never be looked for.
    """
    if not rows or last_listed(rows) is None:
        return True     # no baseline yet: keep trying until one is written
    if rows[-1].get("followers") is not None and followers > rows[-1]["followers"]:
        return True
    last = last_listed(rows)
    return last is not None and followers > last["followers"]


def list_base(rows: list[dict], followers: int) -> int:
    """The count the increase is measured from: the lower of the previous row
    and the last row whose list was read, so an hour held back is included."""
    counts = [r["followers"] for r in (rows[-1:] + [last_listed(rows) or {}])
              if r.get("followers") is not None]
    return min(counts) if counts else followers


def last_listed(rows: list[dict]) -> dict | None:
    listed = [r for r in rows if r.get("listed") and r.get("followers") is not None]
    return listed[-1] if listed else None


def new_arrivals(newest_first: list[str], known: set[str]) -> list[str]:
    """Ids not seen before, newest first. Order is kept so the file says which
    arrived last."""
    return [i for i in newest_first if i not in known]


def read_me(auth) -> tuple[str | None, int | None]:
    r = x_meter.get(ME_URL, params={"user.fields": "public_metrics"},
                    auth=auth, timeout=30, source="followers-hourly")
    if r.status_code != 200:
        log.error(f"users/me {r.status_code}: {r.text[:200]}")
        return None, None
    d = r.json().get("data") or {}
    return d.get("id"), (d.get("public_metrics") or {}).get("followers_count")


def read_followers(auth, user_id: str, max_results: int,
                   all_pages: bool = False) -> list[str] | None:
    """Follower ids, newest first. None on any error — a partial list is not
    returned as a list."""
    ids: list[str] = []
    token = None
    for _ in range(MAX_PAGES):
        params = {"max_results": max_results}
        if token:
            params["pagination_token"] = token
        r = x_meter.get(FOLLOWERS_URL.format(id=user_id), params=params,
                        auth=auth, timeout=30, source="followers-list")
        if r.status_code != 200:
            log.error(f"followers {r.status_code}: {r.text[:200]}")
            return None
        body = r.json()
        ids += [u["id"] for u in body.get("data") or [] if u.get("id")]
        token = (body.get("meta") or {}).get("next_token")
        if not all_pages or not token:
            return ids
    log.error(f"follower list longer than {MAX_PAGES} pages — no baseline written")
    return None


def run(now: datetime.datetime | None = None) -> int:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    auth = x_auth()
    if not auth:
        log.error("X credentials not available")
        return 1
    user_id, followers = read_me(auth)
    if followers is None:
        return 1
    rows = read_series()
    row = {"at": now.isoformat(), "hour": now.strftime("%Y-%m-%dT%H:00Z"),
           "followers": followers, "listed": False}

    if needs_list(rows, followers):
        # A list read is profiles, and profiles are what the daily cap guards.
        # Held back here, the next hour retries (see needs_list).
        held = x_meter.reads_paused(now) or x_meter.profiles_exhausted(now)
        if held:
            row["list_skipped"] = held
        elif last_listed(rows) is None:
            ids = read_followers(auth, user_id, PAGE_SIZE, all_pages=True)
            if ids is not None:
                row.update(listed=True, baseline_ids=ids)
        else:
            prev = list_base(rows, followers)
            size = max(1, min(PAGE_SIZE, followers - prev + LIST_MARGIN))
            ids = read_followers(auth, user_id, size)
            if ids is not None:
                new = new_arrivals(ids, known_ids(rows))
                row.update(listed=True, new_ids=new)
                # The sized read relies on the newest follower coming first.
                # The API reference (docs.x.com, read 2026-10-12) does not
                # state the order, so a count that rose with no unknown id in
                # the read says so in the row instead of passing as "nobody".
                if not new and followers > prev:
                    row["unmatched"] = followers - prev

    append(row)
    log.info(f"{followers} followers"
             + (f" · new {len(row['new_ids'])}" if row.get("new_ids") else "")
             + (" · baseline" if row.get("baseline_ids") else "")
             + (f" · list held: {row['list_skipped']}" if row.get("list_skipped") else ""))
    return 0


def append(row: dict, path: str | None = None) -> None:
    path = path or series_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    os.chmod(path, 0o640)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] %(levelname)s: %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    sys.exit(run())
