"""What has to be true before the ambassador comments on Moltbook.

Three gates, in the order they get cheaper to fail:

  1. the spam rate   Moltbook marks our comments. On 2026-09-27, 128 of
                     u/moltrust-agent's last 166 carried `is_spam` — 77.1 %,
                     at about 24 comments a day. A measurement that only
                     appears in a Sunday report is a post-mortem; here it runs
                     before anything is written, and a bad rate stops the run.
  2. the daily cap   10, down from roughly 24.
  3. relevance       the comment has to be about what we can actually answer,
                     and the reply has to carry a figure or a named
                     specification. The same bar as the reply radar, because
                     the failure was the same: careful, fluent, and about
                     nothing.

The shape is deliberately the radar's. A reply that says something checkable to
someone who asked something checkable is the only kind worth sending, on either
network.

## Why the spam rate cannot simply block at a threshold

77 % is where this starts. If the gate refused to comment until the rate fell,
nothing would ever comment and the rate would never move — the old comments
would hold it hostage for good.

So the rate is measured over the comments made **after the gate went live**,
and while there are too few of those to judge, a small allowance goes out
anyway. That allowance is what produces the evidence the gate then reads.
"""
from __future__ import annotations

import datetime
import logging
import os
import re

import httpx

from agents import voice_gate

log = logging.getLogger("comment_gate")

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"

# Ten a day, from roughly twenty-four.
DAILY_MAX = 10

# Below this the gate lets comments through; at or above it stops the run and
# says so. 25 % is a quarter of everything we write being called spam, which is
# well past "the classifier is twitchy" and into "we are the problem".
SPAM_BLOCK_PCT = 25.0

# How many post-gate comments must exist before the rate means anything. Under
# this the gate is in its probe phase: PROBE_MAX a day, so evidence accrues
# without a bad run costing much.
SPAM_MIN_SAMPLE = 10
PROBE_MAX = 3

# What we have something to say about. Everything else is someone else's
# conversation and we would be the account that turns up uninvited.
ON_TOPIC_RE = re.compile(
    r"\b(agent[- ]?(identity|identities|authorization|authorisation|trust|credential)"
    r"|erc[- ]?8004|x402|did:|verifiable credential|agent registry|attestation"
    r"|know your agent|agent passport|mandate|delegation|provenance)\b", re.I)

# Nothing that names us goes out. The gate's own rule (d) only guards openers.
PRODUCT_RE = re.compile(r"\b(moltrust|moltguard|moltproof|moltbook|molt)\b", re.I)


def today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


# ── the spam rate ──

def _our_comments(key: str, limit: int = 100) -> list[dict] | None:
    """Our own recent comments, newest first. None when the read fails.

    A failed read is not a clean rate: it has to stop the run rather than be
    counted as zero spam.
    """
    try:
        r = httpx.get(f"{MOLTBOOK_BASE}/comments",
                      params={"author": os.getenv("MOLTBOOK_AUTHOR", "moltrust-agent"),
                              "limit": limit},
                      headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except Exception as e:
        log.error(f"moltbook comment read failed: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        log.error(f"moltbook comments -> {r.status_code}: {r.text[:180]}")
        return None
    data = r.json()
    rows = data.get("comments") if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else None


def _parsed(stamp: str) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def spam_state(state: dict, key: str) -> dict:
    """The rate over post-gate comments, and what it means for this run.

    Returns `mode`: "probe" while the sample is too small to judge, "ok" below
    the threshold, "blocked" at or above it, "unreadable" when Moltbook could
    not be asked.
    """
    live_at = _parsed(state.get("gate_live_at", "")) or datetime.datetime.now(
        datetime.timezone.utc)
    rows = _our_comments(key)
    if rows is None:
        return {"mode": "unreadable", "pct": None, "sample": 0}
    scope = [c for c in rows if (_parsed(c.get("created_at") or "") or live_at) >= live_at]
    sample = len(scope)
    if sample < SPAM_MIN_SAMPLE:
        return {"mode": "probe", "pct": None, "sample": sample}
    spam = sum(1 for c in scope if c.get("is_spam"))
    pct = round(100.0 * spam / sample, 1)
    return {"mode": "blocked" if pct >= SPAM_BLOCK_PCT else "ok",
            "pct": pct, "sample": sample, "spam": spam}


def run_allowance(state: dict, key: str) -> tuple[int, str, dict]:
    """(how many comments this run may post, why, the spam reading).

    Zero with a reason is the normal outcome of a bad day, not an error.
    """
    reading = spam_state(state, key)
    used = int((state.get("comments_per_day") or {}).get(today(), 0))

    if reading["mode"] == "unreadable":
        return 0, "Moltbook's own comment list could not be read — not commenting blind", reading
    if reading["mode"] == "blocked":
        return (0, f"{reading['pct']} % of our last {reading['sample']} comments are "
                   f"marked spam (limit {SPAM_BLOCK_PCT} %)", reading)
    cap = PROBE_MAX if reading["mode"] == "probe" else DAILY_MAX
    room = max(0, cap - used)
    if room == 0:
        return 0, f"daily cap reached ({used}/{cap})", reading
    label = "probe phase" if reading["mode"] == "probe" else f"{reading['pct']} % spam"
    return room, f"{room} left today ({used}/{cap}, {label})", reading


def count_comment(state: dict) -> None:
    per_day = state.setdefault("comments_per_day", {})
    day = today()
    per_day[day] = int(per_day.get(day, 0)) + 1
    state["comments_per_day"] = {k: v for k, v in per_day.items() if k >= day}


def arm(state: dict) -> None:
    """Stamp when the gate first ran, so the rate has a start."""
    state.setdefault("gate_live_at",
                     datetime.datetime.now(datetime.timezone.utc).isoformat())


# ── relevance ──

def worth_answering(comment_text: str) -> tuple[bool, str]:
    """Whether the comment is about something we can answer with a fact."""
    text = (comment_text or "").strip()
    if len(text.split()) < 6:
        return False, "too short to carry a question"
    if not ON_TOPIC_RE.search(text):
        return False, "not about agent trust"
    return True, ""


def check_reply(text: str, sources: dict[str, str] | None = None) -> tuple[bool, list[str]]:
    """Both gates over the drafted comment, in reply mode.

    `sources` is passed straight to rule (h): a claim has to appear in a page
    the draft named. An empty mapping means nothing was fetched, and (h) then
    blocks any claim — which is the intended answer when we cite from memory.
    """
    problems = []
    if PRODUCT_RE.search(text or ""):
        problems.append("names one of our products")
    try:
        result = voice_gate.scan([text], mode="reply", sources=sources or {})
    except Exception as e:
        # A gate that cannot load its rules must not wave the comment through.
        return False, [f"voice gate unavailable: {type(e).__name__}: {e}"]
    problems += list(result.get("violations") or [])
    return not problems, problems
