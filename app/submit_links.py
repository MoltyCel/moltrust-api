"""Submit links (Hacker News and other targets) and the one message per post.

On 2026-10-09 the same HN submit link reached Telegram three times in seven
minutes: scripts/telegram_hn_remind.py sent at module level, with no record
of having sent, so every execution of the file sent again. The title also
carried '+' for spaces (quote_plus), which lands literally in the HN form, and
was longer than HN's 80 characters. WORKFLOW 17.

Three rules here, for every generated submit link:
- the title is at most 80 characters; a longer one is cut at a word boundary
  and the cut is logged, never silently;
- the title and URL are encoded with quote(), so a space is %20, never '+';
- one message per post: the sent state is persistent, an attempt is recorded
  before it is made, a retry happens only after a recorded failure, and at
  most MAX_ATTEMPTS attempts are made per post.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import urllib.parse

from app import notify, paths

log = logging.getLogger("moltrust.submit_links")

TITLE_MAX = 80
MAX_ATTEMPTS = 3
HN_SUBMIT = "https://news.ycombinator.com/submitlink"


def state_path() -> str:
    return paths.data("submit_links_sent.json")


def clip_title(title: str, limit: int = TITLE_MAX) -> str:
    """At most `limit` characters, cut at a word boundary, the cut logged."""
    title = " ".join(str(title).split())
    if len(title) <= limit:
        return title
    cut = title[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    cut = cut.rstrip(" ,;:-–—(")
    log.warning("submit title cut from %d to %d characters: %r -> %r",
                len(title), len(cut), title, cut)
    return cut


def hn_submit_link(url: str, title: str) -> str:
    """HN submit link with %20 for spaces (quote, never quote_plus)."""
    t = clip_title(title)
    return (f"{HN_SUBMIT}?u={urllib.parse.quote(url, safe='')}"
            f"&t={urllib.parse.quote(t, safe='')}")


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def _save(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def send_once(key: str, text: str, *, channel: str, path: str | None = None,
              sender=None, now: dt.datetime | None = None) -> str:
    """Send `text` for post `key` unless that post was already announced.

    Returns: "sent", "already-sent", "failed", "gave-up" (MAX_ATTEMPTS
    failures), or "outcome-unknown" (an attempt was recorded but neither its
    success nor its failure — a crash mid-send; not retried, because it may
    have gone out).
    """
    path = path or state_path()
    sender = sender or notify.send_telegram
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    state = _load(path)
    entry = state.get(key) or {"attempts": 0}
    if entry.get("sent_at"):
        log.info("submit link for %s already sent at %s", key, entry["sent_at"])
        return "already-sent"
    if entry.get("attempts", 0) and entry.get("last_started_at") and \
            entry.get("last_failed_at") != entry.get("last_started_at"):
        log.warning("submit link for %s: attempt at %s has no recorded outcome; "
                    "not retried", key, entry["last_started_at"])
        return "outcome-unknown"
    if entry.get("attempts", 0) >= MAX_ATTEMPTS:
        log.warning("submit link for %s: %d failed attempts, giving up",
                    key, entry["attempts"])
        return "gave-up"
    entry["attempts"] = entry.get("attempts", 0) + 1
    entry["last_started_at"] = stamp
    state[key] = entry
    _save(path, state)          # recorded before the send, so a crash cannot repeat it
    ok = bool(sender(text, channel=channel))
    state = _load(path)
    entry = state.get(key, entry)
    if ok:
        entry["sent_at"] = stamp
    else:
        entry["last_failed_at"] = stamp
    state[key] = entry
    _save(path, state)
    return "sent" if ok else "failed"
