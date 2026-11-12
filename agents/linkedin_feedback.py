"""A Telegram reply to a LinkedIn draft gives a new version (ops CN1-LI).

Herkunft: ops CN1-LI, Freigabe Lars 09.11.2026 (CO10) und 12.11.2026. CN1
(#1025) built the back-channel for X reply drafts only; a reply from Lars to a
LinkedIn draft message (agents/syndicate.py, art `linkedin-entwurf`) had no
reader and was lost.

Same rules as CN1:
  - matched by message id: a reply to the draft message or to any later
    version of it; replies to other messages and from other senders do nothing
  - the new version replies to the message Lars answered, change line first,
    and it goes through the voice gate again; only a passing version carries
    the Posten button and replaces the parked text
  - a LinkedIn draft has no expiry: it stays open until posted or discarded
  - one line per version in data/linkedin_feedback.jsonl

Called from agents/reply_radar.py next to consume_feedback(); its own claim
takes only replies to LinkedIn draft messages, so the two never race.
"""
from __future__ import annotations

import datetime
import html
import json
import logging
import os

from app import notify, paths, telegram_inbox
from agents import linkedin_post, linkedin_rules, reply_decisions, voice_gate

log = logging.getLogger("linkedin_feedback")

CONSUMER = "linkedin_feedback"
VERSIONS = "linkedin_feedback.jsonl"
FEEDBACK_MAX = reply_decisions.FREE_TEXT_MAX


def versions_path() -> str:
    return paths.data(VERSIONS)


def from_lars(m: dict) -> bool:
    """The private drafts chat: chat id == Lars's user id, never a bot."""
    sender = m.get("from") or {}
    chat = str(notify.chat_id_for(notify.STATS) or "").strip()
    return (bool(chat) and not sender.get("is_bot")
            and str(sender.get("id", "")) == chat
            and str((m.get("chat") or {}).get("id", "")) == chat)


def awaiting() -> dict[int, str]:
    """Message id -> draft key, for every open LinkedIn draft message."""
    out: dict[int, str] = {}
    for key, entry in linkedin_post.load_pending().items():
        if entry.get("result"):
            continue
        for mid in [entry.get("message_id")] + [
                v.get("message_id") for v in entry.get("versions") or []]:
            if mid is not None:
                out[int(mid)] = key
    return out


def _record(key: str, version: int, feedback: str, text: str | None,
            change: str | None, ok: bool | None, problems: list[str],
            message_id) -> None:
    row = {"draft_id": key, "kind": "linkedin_version", "version": version,
           "feedback": feedback, "text": text, "change": change, "ok": ok,
           "problems": problems, "message_id": message_id,
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    path = paths.ensure(versions_path())
    with open(path, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.chmod(path, 0o640)


def _gate(text: str, entry: dict) -> tuple[bool, list[str], str]:
    from agents import syndicate
    source = syndicate.fetch_article_text(entry["source"]) if entry.get("source") else ""
    scan = voice_gate.scan([text], source_text=source or None, mode="post",
                           max_chars=syndicate.LINKEDIN_MAX_CHARS)
    # CO7/CO9 hold for every version, not only the first (ops CO-LI).
    problems = list(scan["violations"]) + linkedin_rules.text_problems(text)
    report = voice_gate.format_report(scan)
    extra = linkedin_rules.text_problems(text)
    if extra:
        report += "\n" + "\n".join(extra)
    return not problems, problems, report


def redraft(key: str, entry: dict, feedback: str, reply_to: int) -> int | None:
    """New version after feedback. Returns its number, None if none was made."""
    from agents import syndicate
    drafted = syndicate.redraft_linkedin(entry, feedback)
    if not drafted:
        notify.send_telegram_message(
            "Keine neue Fassung: der Entwurf kam nicht zurück (siehe Log).",
            channel=notify.STATS, reply_to=reply_to, art="befehl-antwort")
        log.info(f"  linkedin {key}: feedback, no new version")
        return None
    text, change = drafted
    change = change or f"nach Rückmeldung „{feedback[:80]}“"
    ok, problems, report = _gate(text, entry)
    version = int(entry.get("version") or 1) + 1
    title = html.escape(entry.get("title") or "")
    head = (f"✏️ Fassung {version}: {html.escape(change)}\n\n"
            + ("\U0001f4dd <b>LinkedIn-Entwurf</b>" if ok
               else "⚠️ <b>LinkedIn-Entwurf — Gate blockiert</b>")
            + f": {title}\n<pre>{html.escape(text[:2500])}</pre>\n")
    if ok:
        body = head + "Voice-Gate bestanden. Posten oder verwerfen über die Knöpfe darunter."
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Posten", "callback_data": f"li|post|{key}"},
            {"text": "\U0001f5d1 Verwerfen", "callback_data": f"li|drop|{key}"},
        ]]}
        mid = notify.send_telegram_message(body, channel=notify.STATS, parse_mode="HTML",
                                           reply_markup=keyboard, reply_to=reply_to,
                                           art="linkedin-entwurf")
    else:
        # A blocked version carries no Posten button and does not replace the
        # parked text: the button posts the last version that passed.
        body = head + "<pre>" + html.escape(report[:900]) + "</pre>"
        mid = notify.send_telegram_message(body, channel=notify.STATS, parse_mode="HTML",
                                           reply_to=reply_to, art="entwurf-blockiert")
    pending = linkedin_post.load_pending()
    cur = pending.get(key)
    if cur is not None:
        cur["version"] = version
        cur.setdefault("versions", []).append(
            {"version": version, "message_id": mid, "ok": ok, "change": change,
             "feedback": feedback})
        if ok:
            cur["text"] = text
        linkedin_post.save_pending(pending)
    _record(key, version, feedback, text, change, ok, problems, mid)
    log.info(f"  linkedin {key}: version {version} after feedback: "
             f"{'PASS' if ok else 'BLOCKED'}")
    return version


STALE_RESULT = "verworfen"


def sweep_stale(now: datetime.datetime | None = None) -> list[str]:
    """CO8: close every open LinkedIn draft whose source is older than seven
    days or has no date. It is not shown again; one line per closed draft."""
    pending = linkedin_post.load_pending()
    closed = []
    for key, entry in pending.items():
        if entry.get("result"):
            continue
        problem = linkedin_rules.source_problem(
            {"source": entry.get("source"), "source_date": entry.get("source_date")},
            now=now)
        if not problem:
            continue
        entry["result"] = STALE_RESULT
        entry["closed_reason"] = f"CO8: {problem}"
        closed.append(key)
        _record(key, int(entry.get("version") or 1), None, None,
                f"geschlossen, CO8: {problem}", None, [], None)
        log.info(f"  linkedin {key}: closed, CO8 {problem} ({entry.get('title')})")
    if closed:
        linkedin_post.save_pending(pending)
    return closed


def consume() -> int:
    """Replies from Lars to open LinkedIn draft messages. Returns how many acted on."""
    sweep_stale()
    waiting = awaiting()
    if not waiting:
        return 0
    updates = telegram_inbox.claim(CONSUMER, ["message"], limit=20,
                                   reply_to=list(waiting))
    done = 0
    for u in updates:
        m = u.get("message") or {}
        ref = (m.get("reply_to_message") or {}).get("message_id")
        key = waiting.get(int(ref)) if ref is not None else None
        text = (m.get("text") or "").strip()
        if not key or not text:
            continue
        if not from_lars(m):
            log.info(f"  linkedin {key}: reply from another sender, ignored")
            continue
        pending = linkedin_post.load_pending()
        entry = pending.get(key)
        if entry is None or entry.get("result"):
            continue
        if reply_decisions.is_discard(text):
            entry["result"] = "verworfen"
            linkedin_post.save_pending(pending)
            _record(key, int(entry.get("version") or 1), text, None, None,
                    None, [], None)
            notify.send_telegram_message("\U0001f5d1 Verworfen, Entwurf geschlossen.",
                                         channel=notify.STATS, reply_to=int(ref),
                                         art="befehl-antwort")
            done += 1
            continue
        entry.setdefault("feedback", []).append(
            {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "text": text[:FEEDBACK_MAX], "message_id": m.get("message_id")})
        linkedin_post.save_pending(pending)
        redraft(key, entry, text[:FEEDBACK_MAX], int(ref))
        done += 1
    return done
