"""Moltbook replies that wait for a button (ops MB1).

The Ambassador gives up on a comment after its gate blocked three drafts. The
comment then stays unanswered for good, and W9 reports it every hour: on
2026-10-26 eight comments had waited up to seven days, each one a draft the
gate refused for length, a figure or a sentence pattern (logs/ambassador.log).

This is the second way out for such a comment. A draft is written outside the
Ambassador, scanned by the same voice gate as a post, parked here and sent to
Telegram with Senden and Verwerfen. Nothing goes to Moltbook unless somebody
presses Senden, the same rule as the LinkedIn and X drafts (ops CM2).

    python3 agents/moltbook_drafts.py --offer FILE        # scan, park, send
    python3 agents/moltbook_drafts.py --discard ID REASON  # dropped without Lars
    python3 agents/moltbook_drafts.py --list

The button press arrives in agents/reply_radar.py, which owns every
callback_query (see the comment above handle_linkedin there), and is handed to
handle() below.

Every decision, sent or dropped, is kept by comment id. W9 reads decided_ids():
a comment that was dropped is not owed a reply, and without that a single
Verwerfen would keep the watchdog red for as long as the post is in its window.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import notify, paths

log = logging.getLogger("moltbook_drafts")

PENDING = "moltbook_pending.json"

# A Moltbook comment has no 280-character limit, but the Ambassador's own gate
# stops at 150 words (rule c4). 1200 characters is about that length, so a
# draft that passes here is not longer than one the Ambassador may write.
MAX_CHARS = 1200

# A comment id is a UUID, 36 characters. "mb|send|" adds 8: 44 of the 64 bytes
# Telegram allows for callback_data.
PREFIX = "mb|"

DECIDED = ("gesendet", "verworfen", "verworfen (Console)")


def pending_path() -> str:
    return paths.data(PENDING)


def load_pending() -> dict:
    try:
        with open(pending_path()) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_pending(d: dict) -> None:
    path = paths.ensure(pending_path())
    with open(path, "w") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.chmod(path, 0o640)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def decided_ids() -> set[str]:
    """Comment ids with a final decision. Read by W9 in agents/watchdog.py."""
    return {cid for cid, e in load_pending().items() if e.get("result") in DECIDED}


def scan(entry: dict) -> dict:
    """The post gate over the draft, with the comment and our post as source.

    The Ambassador's g2g refused the 22.10 draft for "$60 million" although the
    figure was in the comment it answered. Comment and post together are what
    a reply may quote, so both are the source text.
    """
    from agents import voice_gate
    source = "\n\n".join(x for x in (entry.get("comment"), entry.get("post_text")) if x)
    return voice_gate.scan([entry["draft"]], source_text=source or None,
                           mode="post", max_chars=MAX_CHARS)


def message(entry: dict, report: str, ok: bool) -> str:
    head = ("\U0001f4ac <b>Moltbook-Antwort, Entwurf</b>" if ok
            else "\u26a0\ufe0f <b>Moltbook-Antwort — Gate blockiert</b>")
    return (f"{head}\n"
            f"{html.escape(entry.get('author') or '?')} am "
            f"{html.escape((entry.get('created_at') or '')[:16])} UTC unter "
            f"„{html.escape(entry.get('post_title') or '')}“\n\n"
            f"<b>Kommentar</b>\n<pre>{html.escape((entry.get('comment') or '')[:1200])}</pre>\n\n"
            f"<b>Antwort</b>\n<pre>{html.escape(entry['draft'])}</pre>\n\n"
            f"{html.escape(report)}")


def offer(entries: list[dict], send=None) -> int:
    """Scan, park and send each draft, oldest comment first. Returns how many
    went out with buttons.

    A blocked draft is still delivered, without Senden: it can be read and
    judged, and a blocked draft with the button is one tap from being posted.
    """
    send = send or notify.send_telegram_message
    from agents import voice_gate
    pending = load_pending()
    offered = 0
    for e in sorted(entries, key=lambda x: x.get("created_at") or ""):
        cid = e["comment_id"]
        if pending.get(cid, {}).get("result"):
            log.info(f"{cid}: already {pending[cid]['result']}, not offered again")
            continue
        result = scan(e)
        ok = not result["violations"]
        report = voice_gate.format_report(result)
        keyboard = {"inline_keyboard": [[
            *([{"text": "\u2705 Senden", "callback_data": f"{PREFIX}send|{cid}"}] if ok else []),
            {"text": "\U0001f5d1 Verwerfen", "callback_data": f"{PREFIX}drop|{cid}"},
        ]]}
        pending[cid] = {k: e.get(k) for k in ("post_id", "post_title", "author",
                                              "created_at", "comment", "draft")}
        pending[cid].update({"offered_at": _now(), "gate_ok": ok})
        save_pending(pending)
        sent = send(message(e, report, ok), channel=notify.STATS,
                    parse_mode="HTML", reply_markup=keyboard)
        log.info(f"{cid}: offered, gate {'PASS' if ok else 'BLOCKED'}, "
                 f"telegram {'ok' if sent else 'FAILED'}")
        offered += bool(ok and sent)
    return offered


def discard(cid: str, reason: str) -> None:
    """A comment the Console drops itself: only an advert or empty praise (MB1
    criterion 3). Kept with its reason, never sent to Lars."""
    pending = load_pending()
    entry = pending.setdefault(cid, {})
    entry.update({"result": "verworfen (Console)", "reason": reason, "decided_at": _now()})
    save_pending(pending)


def _edit(cq: dict, text: str) -> None:
    from agents import reply_radar
    msg = cq.get("message") or {}
    reply_radar.edit_message((msg.get("chat") or {}).get("id"), msg.get("message_id"), text)


def _post(entry: dict, cid: str) -> dict | None:
    """Through the Ambassador's own last gate (links, install lines, credit
    offers) and its verification solver. Imported on use: ambassador imports
    reply_radar, which imports this module's caller."""
    import httpx
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import ambassador
    ambassador.init_keys()
    with httpx.Client() as client:
        return ambassador.post_reply(client, entry["post_id"], entry["draft"], cid)


def handle(cq: dict, data: str, dry_run: bool = False, post=None) -> None:
    """A button press on a Moltbook draft. The only path to the write."""
    post = post or _post
    try:
        _, verb, cid = data.split("|", 2)
    except ValueError:
        log.warning(f"unparseable moltbook callback: {data[:40]}")
        return
    pending = load_pending()
    entry = pending.get(cid)
    if not entry or not entry.get("draft"):
        _edit(cq, "\u26a0\ufe0f Entwurf nicht mehr im Speicher — nichts gesendet.")
        log.warning(f"{cid}: no draft parked")
        return
    if entry.get("result"):
        # A re-delivered callback or a second press must not reply twice.
        log.info(f"{cid}: already {entry['result']}")
        return
    if verb == "drop":
        entry.update({"result": "verworfen", "decided_at": _now()})
        save_pending(pending)
        _edit(cq, f"\U0001f5d1 Verworfen — Antwort an {entry.get('author') or cid}")
        log.info(f"{cid}: verworfen")
        return
    if verb != "send" or not entry.get("gate_ok"):
        return
    if dry_run:
        log.info(f"{cid}: dry run, nothing sent")
        return
    try:
        result = post(entry, cid)
    except Exception as e:
        # ReplyWithheld included: the draft stays parked without a result, so
        # the comment is still owed and W9 keeps counting it.
        entry["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        save_pending(pending)
        _edit(cq, f"\u26a0\ufe0f Nicht gesendet: {entry['error']}")
        log.error(f"{cid}: {entry['error']}")
        return
    if not result:
        entry["error"] = "Moltbook hat nicht bestätigt"
        save_pending(pending)
        _edit(cq, "\u26a0\ufe0f Nicht gesendet: Moltbook hat nicht bestätigt.")
        log.error(f"{cid}: no confirmation from Moltbook")
        return
    reply = result.get("comment", result) if isinstance(result, dict) else {}
    entry.update({"result": "gesendet", "decided_at": _now(),
                  "reply_id": reply.get("id") if isinstance(reply, dict) else None})
    entry.pop("error", None)
    save_pending(pending)
    _edit(cq, f"\u2705 Auf Moltbook gesendet — Antwort an {entry.get('author') or cid}")
    log.info(f"{cid}: gesendet {entry.get('reply_id')}")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offer", metavar="FILE")
    ap.add_argument("--discard", nargs=2, metavar=("ID", "REASON"))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.offer:
        with open(a.offer) as f:
            entries = json.load(f)
        print(f"{offer(entries)} von {len(entries)} mit Knöpfen gesendet")
        return 0
    if a.discard:
        discard(*a.discard)
        print(f"{a.discard[0]}: verworfen (Console)")
        return 0
    for cid, e in sorted(load_pending().items(), key=lambda x: x[1].get("created_at") or ""):
        print(f"{cid}  {e.get('author') or '?':<22} {e.get('result') or 'offen'}"
              f"{'  Gate blockiert' if e.get('gate_ok') is False else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
