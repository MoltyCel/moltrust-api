"""One line per reply draft, and the reason when a draft is discarded (ops CM6).

The goal is a file that, after four weeks, separates the two ways the reply
radar can fail: drafts that are weak, and a channel that does not carry. A
discard without a reason cannot tell those apart, so a press on Verwerfen is
followed by one question with four short keys and a free-text key. No answer
within twelve hours is recorded as "ohne Grund" — a missing reason is a value,
not a gap in the file.

Two files, both under data/:

  reply_radar_drafts_open.json    the drafts handed out and not yet closed,
                                  with the reason question when one is asked
  reply_radar_decisions.jsonl     one line per draft, written when it closes:
                                  gesendet, verworfen or verfallen

Why a file of its own rather than a key in reply_radar_state.json: the 06:05
run and the five-minute consumer both load that state, work, and write it back
whole. A draft registered by the run and a draft closed by the consumer in the
same minutes would overwrite each other. This file is read and written only
under an exclusive lock, and the decisions file refuses a second line for a
draft that already has one, so a lost update cannot turn into a missing or a
duplicate line.

The interim report of the CM5 measurement reads the decisions file through
summary() below (CM6 criterion 4).

**A posted reply beats the clock (ops CM23, Lars 31.10.2026).** Since CM16 a
draft goes out with an intent link and Lars posts it himself; nothing presses
a key, so the twelve-hour sweep would book every posted draft as verfallen.
close_posted() closes a draft as gesendet with route "link" once a reply from
@LarsOnMoltrust to its target post is found (agents/lars_reply_match.py). A
reply found only after the sweep has already written verfallen overrides that
line, within LINK_LOOKBACK_HOURS: the file then carries a second line for the
draft with `overrides: verfallen`, and every reader takes the last line per
draft. That is the one exception to "one line per draft", and it only ever
turns verfallen into gesendet.
"""
from __future__ import annotations

import collections
import contextlib
import datetime
import fcntl
import json
import os
import re

from app import paths

# CM6 criterion 2, Lars 22.10.2026: four short keys and free text.
REASONS = {
    "ton": "Ton",
    "inhalt": "Inhalt falsch",
    "ziel": "Zielpost unpassend",
    "timing": "Timing",
}
FREE_TEXT = "frei"
NO_REASON = "ohne Grund"
REASON_TTL_HOURS = 12
# A draft with no press at all closes as verfallen after the same twelve hours
# the send key lives (CM2, reply_radar.DRAFT_TTL_HOURS).
DRAFT_TTL_HOURS = 12
FREE_TEXT_MAX = 500

SENT, DROPPED, EXPIRED = "gesendet", "verworfen", "verfallen"
RESULTS = (SENT, DROPPED, EXPIRED)

# CM23: posted by Lars through the intent link, found on his timeline.
ROUTE_LINK = "link"
# How far back a reply may still turn a verfallen draft into a posted one,
# counted from the draft's offer. Three days: a reply to a post older than
# that is no longer an answer to the draft, and the timeline read stays short.
LINK_LOOKBACK_HOURS = 72

GATE_PASSED, GATE_FINDINGS = "bestanden", "Befunde"

_SOURCE_URL_RE = re.compile(r"^Quelle \([a-z]+\): (\S+)", re.M)


def open_path() -> str:
    return paths.data("reply_radar_drafts_open.json")


def decisions_path() -> str:
    return paths.data("reply_radar_decisions.jsonl")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parsed(stamp) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def draft_key(tweet_id: str) -> str:
    """The draft ID in the decisions file. One draft per target post: the run
    marks a target seen once it has drafted for it."""
    return f"rr-{tweet_id}"


@contextlib.contextmanager
def _registry():
    """The open drafts, read and written back under one exclusive lock."""
    path = paths.ensure(open_path())
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                with open(path) as f:
                    reg = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                reg = {}
            reg.setdefault("drafts", {})
            yield reg
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(reg, f, indent=1, sort_keys=True)
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read_lines() -> list[dict]:
    rows = []
    try:
        with open(decisions_path()) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return rows


def _append_once(row: dict) -> bool:
    """Append the line unless this draft has one already. Called under the lock."""
    if any(r.get("draft_id") == row["draft_id"] for r in _read_lines()):
        return False
    with open(paths.ensure(decisions_path()), "a") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def _line(entry: dict, result: str, reason: str | None, now: datetime.datetime,
          **extra) -> dict:
    return {**extra,
        "draft_id": entry["draft_id"],
        "target": entry.get("target"),
        "at": now.isoformat(),
        "offered_at": entry.get("offered_at"),
        "result": result,
        "reason": reason,
        "voice_gate": entry.get("voice_gate"),
        "voice_gate_findings": entry.get("voice_gate_findings") or [],
    }


def gate_from_message(text: str) -> tuple[str | None, list[str]]:
    """The voice-gate result as the draft message states it (CM2 criterion 2)."""
    if "Voice-Gate: bestanden" in (text or ""):
        return GATE_PASSED, []
    if "Voice-Gate: Befunde" in (text or ""):
        return GATE_FINDINGS, []
    return None, []


def _entry_from_message(tweet_id: str, msg: dict) -> dict:
    """A draft sent before this file existed still gets its line."""
    text = msg.get("text") or ""
    m = _SOURCE_URL_RE.search(text)
    gate, findings = gate_from_message(text)
    stamp = msg.get("date")
    try:
        offered = datetime.datetime.fromtimestamp(int(stamp), datetime.timezone.utc).isoformat()
    except (TypeError, ValueError):
        offered = None
    return {"draft_id": draft_key(tweet_id), "tweet_id": tweet_id,
            "target": m.group(1) if m else f"https://x.com/i/status/{tweet_id}",
            "offered_at": offered, "voice_gate": gate,
            "voice_gate_findings": findings,
            "chat_id": (msg.get("chat") or {}).get("id"),
            "message_id": msg.get("message_id")}


# ── what the radar calls ─────────────────────────────────────────────────────

def register(tweet: dict, ok: bool, problems: list[str], message_id,
             chat_id=None, now: datetime.datetime | None = None) -> None:
    """A draft went out to Telegram. From here it is open until it closes."""
    now = now or _now()
    tweet_id = str(tweet["id"])
    with _registry() as reg:
        reg["drafts"].setdefault(tweet_id, {
            "draft_id": draft_key(tweet_id),
            "tweet_id": tweet_id,
            "target": f"https://x.com/{tweet.get('_author') or 'i'}/status/{tweet_id}",
            "offered_at": now.isoformat(),
            "voice_gate": GATE_PASSED if ok and not problems else GATE_FINDINGS,
            "voice_gate_findings": list(problems or []),
            "message_id": message_id,
            "chat_id": chat_id,
        })


def close(tweet_id: str, result: str, reason: str | None = None,
          msg: dict | None = None, now: datetime.datetime | None = None) -> bool:
    """Write the one line for this draft and take it off the open list.

    False when the draft already has its line — a second press, or a sweep
    that raced a press.
    """
    if result not in RESULTS:
        raise ValueError(f"unknown result {result!r}")
    now = now or _now()
    tweet_id = str(tweet_id)
    with _registry() as reg:
        entry = reg["drafts"].pop(tweet_id, None) or _entry_from_message(tweet_id, msg or {})
        return _append_once(_line(entry, result, reason, now))


def link_candidates(now: datetime.datetime | None = None) -> dict[str, str]:
    """Target id -> offered_at for every draft a reply from Lars could close.

    The open drafts, plus those the sweep booked verfallen whose offer lies
    within LINK_LOOKBACK_HOURS. A draft without an offer time is left out: it
    cannot be told apart from a reply Lars wrote before the draft existed.
    """
    now = now or _now()
    cutoff = now - datetime.timedelta(hours=LINK_LOOKBACK_HOURS)
    out: dict[str, str] = {}
    with _registry() as reg:
        for tid, entry in reg["drafts"].items():
            if _parsed(entry.get("offered_at")):
                out[str(tid)] = entry["offered_at"]
    for key, row in _latest_lines().items():
        offered = _parsed(row.get("offered_at"))
        if (row.get("result") == EXPIRED and offered and offered >= cutoff
                and key.startswith("rr-")):
            out.setdefault(key[3:], row["offered_at"])
    return out


def close_posted(tweet_id: str, reply_id: str, link: str, posted_at: str | None,
                 now: datetime.datetime | None = None) -> str | None:
    """CM23: a reply from Lars to the draft's target post was found.

    Returns "closed" for an open draft, "overridden" when a verfallen line
    within the lookback is replaced, None when nothing changed (no such draft,
    already gesendet, discarded, or older than the lookback).
    """
    now = now or _now()
    tweet_id = str(tweet_id)
    extra = {"route": ROUTE_LINK, "reply_id": str(reply_id), "link": link,
             "posted_at": posted_at}
    with _registry() as reg:
        entry = reg["drafts"].pop(tweet_id, None)
        if entry is not None and _append_once(_line(entry, SENT, None, now, **extra)):
            return "closed"
        last = _latest_lines().get(draft_key(tweet_id))
        if not last or last.get("result") != EXPIRED:
            return None
        offered = _parsed(last.get("offered_at"))
        if not offered or now - offered > datetime.timedelta(hours=LINK_LOOKBACK_HOURS):
            return None
        row = _line(last, SENT, None, now, overrides=EXPIRED, **extra)
        with open(paths.ensure(decisions_path()), "a") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return "overridden"


def _latest_lines() -> dict[str, dict]:
    """The last line per draft: an override (CM23) replaces a verfallen line."""
    out: dict[str, dict] = {}
    for row in _read_lines():
        if row.get("draft_id"):
            out[row["draft_id"]] = row
    return out


def is_closed(tweet_id: str) -> bool:
    key = draft_key(str(tweet_id))
    return any(r.get("draft_id") == key for r in _read_lines())


def mark_asked(tweet_id: str, msg: dict, question_message_id,
               now: datetime.datetime | None = None) -> None:
    """Verwerfen was pressed and the reason question is out."""
    now = now or _now()
    tweet_id = str(tweet_id)
    with _registry() as reg:
        entry = reg["drafts"].get(tweet_id) or _entry_from_message(tweet_id, msg or {})
        entry.update({"asked_at": now.isoformat(),
                      "question_message_id": question_message_id})
        reg["drafts"][tweet_id] = entry


def mark_prompted(tweet_id: str, prompt_message_id) -> None:
    """The free-text key was pressed and the reply prompt is out."""
    with _registry() as reg:
        entry = reg["drafts"].get(str(tweet_id))
        if entry is not None:
            entry["prompt_message_id"] = prompt_message_id


def awaiting_reason() -> dict[int, str]:
    """Message id -> tweet id, for every message a free-text answer may reply to."""
    out: dict[int, str] = {}
    with _registry() as reg:
        for tid, entry in reg["drafts"].items():
            if not entry.get("asked_at"):
                continue
            for key in ("question_message_id", "prompt_message_id"):
                mid = entry.get(key)
                if mid is not None:
                    out[int(mid)] = tid
    return out


def asked_entry(tweet_id: str) -> dict | None:
    with _registry() as reg:
        entry = reg["drafts"].get(str(tweet_id))
        return dict(entry) if entry and entry.get("asked_at") else None


def due(now: datetime.datetime | None = None) -> list[tuple[str, str, str | None, dict]]:
    """Close what twelve hours have decided. Returns (tweet_id, result, reason, entry).

    An asked draft without an answer is verworfen "ohne Grund"; a draft nobody
    pressed anything on is verfallen.
    """
    now = now or _now()
    closed = []
    with _registry() as reg:
        for tid, entry in list(reg["drafts"].items()):
            if entry.get("asked_at"):
                start, ttl, result, reason = (entry["asked_at"], REASON_TTL_HOURS,
                                              DROPPED, NO_REASON)
            else:
                start, ttl, result, reason = (entry.get("offered_at"), DRAFT_TTL_HOURS,
                                              EXPIRED, None)
                # ops RR-KEEP: a draft sent again carries its own end. Only
                # later than the twelve hours, never earlier.
                until = _parsed(entry.get("expires_at"))
                if until is not None and now <= until:
                    continue
            when = _parsed(start)
            if when is not None and now - when <= datetime.timedelta(hours=ttl):
                continue
            reg["drafts"].pop(tid)
            if _append_once(_line(entry, result, reason, now)):
                closed.append((tid, result, reason, entry))
    return closed


# ── what the CM5 report reads ────────────────────────────────────────────────

def summary(start: datetime.datetime, until: datetime.datetime) -> dict:
    """Count per result and the discard reasons by frequency, for [start, until)."""
    counts = {r: 0 for r in RESULTS}
    reasons: collections.Counter = collections.Counter()
    for row in _latest_lines().values():
        when = _parsed(row.get("at"))
        if when is None or not (start <= when < until):
            continue
        result = row.get("result")
        if result not in counts:
            continue
        counts[result] += 1
        if result == DROPPED:
            reasons[row.get("reason") or NO_REASON] += 1
    ranked = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"counts": counts, "reasons": ranked}


def format_summary(s: dict) -> list[str]:
    c = s["counts"]
    lines = [f"Entwürfe: gesendet {c[SENT]}, verworfen {c[DROPPED]}, "
             f"verfallen {c[EXPIRED]}"]
    if s["reasons"]:
        lines.append("Gründe der Verwürfe: "
                     + ", ".join(f"{why} {n}" for why, n in s["reasons"]))
    else:
        lines.append("Gründe der Verwürfe: keine Verwürfe")
    return lines
