"""CN1-LI: a Telegram reply to a LinkedIn draft gives a new version.

Herkunft: ops CN1-LI (Freigabe Lars 09.11. und 12.11.2026). CN1 (#1025) read
replies to X reply drafts only; a reply to a LinkedIn draft message
(agents/syndicate.py, art `linkedin-entwurf`) had no reader.

  K2  matched by message id; replies to other messages and from other
      senders do nothing
  K3  new version replies to the draft message, change line first, voice gate
      again; only a passing version carries Posten and replaces the text
  K4  no expiry: open until posted or discarded
  K5  one line per version in data/linkedin_feedback.jsonl
"""
import json

import pytest

from agents import linkedin_feedback as lf
from agents import linkedin_post, syndicate

LARS = 4711
DRAFT_MID = 900
KEY = "abcdef0123456789"
FIRST = "First LinkedIn text.\n\nhttps://moltrust.ch/blog/x.html"
NEW = "Second LinkedIn text, after feedback.\n\nhttps://moltrust.ch/blog/x.html"


@pytest.fixture
def world(monkeypatch, tmp_path):
    seen = {"sent": [], "claims": [], "redraft": [], "inbox": [], "gate_ok": True}
    monkeypatch.setattr(linkedin_post, "pending_path", lambda: str(tmp_path / "pending.json"))
    monkeypatch.setattr(lf, "versions_path", lambda: str(tmp_path / "versions.jsonl"))

    def fake_send(text, **kw):
        seen["sent"].append({"text": text, **kw})
        return 1000 + len(seen["sent"])

    def fake_claim(consumer, kinds, limit=100, **kw):
        seen["claims"].append((consumer, kinds, kw))
        wanted = {str(m) for m in kw.get("reply_to") or []}
        out = [u for u in seen["inbox"]
               if str(((u.get("message") or {}).get("reply_to_message") or {})
                      .get("message_id")) in wanted]
        for u in out:
            seen["inbox"].remove(u)
        return out

    def fake_redraft(entry, feedback):
        seen["redraft"].append({"previous": entry.get("text"), "feedback": feedback})
        return NEW, "Zahl aus dem Artikel in den ersten Satz"

    monkeypatch.setattr(lf.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(lf.notify, "chat_id_for", lambda ch: str(LARS))
    monkeypatch.setattr(lf.telegram_inbox, "claim", fake_claim)
    monkeypatch.setattr(syndicate, "redraft_linkedin", fake_redraft)
    monkeypatch.setattr(lf, "_gate", lambda text, entry: (
        (True, [], "Voice-Gate: PASS") if seen["gate_ok"]
        else (False, ["(c) contrast pair"], "Voice-Gate: (c) contrast pair")))

    linkedin_post.remember(KEY, FIRST, {"title": "Post X", "link": "https://moltrust.ch/blog/x.html"})
    linkedin_post.note_message(KEY, DRAFT_MID, LARS)
    seen["path"] = tmp_path
    return seen


def reply(text, to=DRAFT_MID, sender=LARS, bot=False, chat=LARS, mid=950):
    return {"update_id": mid, "message": {
        "message_id": mid, "text": text, "chat": {"id": chat, "type": "private"},
        "from": {"id": sender, "is_bot": bot},
        "reply_to_message": {"message_id": to}}}


def versions(tmp_path):
    p = tmp_path / "versions.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ── K2 ──

def test_reply_to_the_draft_message_gives_a_new_version(world):
    world["inbox"].append(reply("Kürzer, Zahl nach vorn"))
    assert lf.consume() == 1
    assert world["redraft"] == [{"previous": FIRST, "feedback": "Kürzer, Zahl nach vorn"}]
    assert world["claims"][0][0] == "linkedin_feedback"
    assert world["claims"][0][2]["reply_to"] == [DRAFT_MID]


def test_reply_to_another_message_does_nothing(world):
    world["inbox"].append(reply("Kürzer", to=12345))
    assert lf.consume() == 0
    assert world["redraft"] == [] and world["sent"] == []


def test_reply_from_another_sender_does_nothing(world):
    world["inbox"].append(reply("Kürzer", sender=999))
    assert lf.consume() == 0
    world["inbox"].append(reply("Kürzer", bot=True, mid=951))
    assert lf.consume() == 0
    assert world["redraft"] == [] and world["sent"] == []


# ── K3 ──

def test_new_version_replies_with_change_line_first_and_buttons(world):
    world["inbox"].append(reply("Kürzer"))
    lf.consume()
    msg = world["sent"][-1]
    assert msg["reply_to"] == DRAFT_MID
    assert msg["text"].startswith("✏️ Fassung 2: Zahl aus dem Artikel")
    assert "Second LinkedIn text" in msg["text"]
    assert msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"li|post|{KEY}"
    assert linkedin_post.load_pending()[KEY]["text"] == NEW


def test_reply_to_the_new_version_is_matched_too(world):
    world["inbox"].append(reply("Kürzer"))
    lf.consume()
    v2_mid = 1000 + len(world["sent"])
    world["inbox"].append(reply("Noch kürzer", to=v2_mid, mid=952))
    assert lf.consume() == 1
    assert world["sent"][-1]["text"].startswith("✏️ Fassung 3:")


def test_blocked_version_has_no_post_button_and_keeps_the_old_text(world):
    world["gate_ok"] = False
    world["inbox"].append(reply("Kürzer"))
    lf.consume()
    msg = world["sent"][-1]
    assert "reply_markup" not in msg or msg["reply_markup"] is None
    assert "Gate blockiert" in msg["text"]
    assert linkedin_post.load_pending()[KEY]["text"] == FIRST


# ── K4 ──

def test_draft_stays_open_after_feedback_and_closes_on_discard(world):
    world["inbox"].append(reply("Kürzer"))
    lf.consume()
    assert DRAFT_MID in lf.awaiting()
    world["inbox"].append(reply("verwerfen", mid=953))
    assert lf.consume() == 1
    assert linkedin_post.load_pending()[KEY]["result"] == "verworfen"
    assert lf.awaiting() == {}


def test_posted_draft_is_not_awaited(world):
    d = linkedin_post.load_pending()
    d[KEY]["result"] = "gepostet"
    linkedin_post.save_pending(d)
    world["inbox"].append(reply("Kürzer"))
    assert lf.consume() == 0


# ── K5 ──

def test_one_line_per_version(world):
    world["inbox"].append(reply("Kürzer"))
    lf.consume()
    rows = versions(world["path"])
    assert len(rows) == 1
    assert rows[0]["draft_id"] == KEY and rows[0]["version"] == 2
    assert rows[0]["feedback"] == "Kürzer" and rows[0]["ok"] is True


# ── the hook in syndicate keeps the message id ──

def test_deliver_linkedin_keeps_the_message_id(monkeypatch, tmp_path):
    monkeypatch.setattr(linkedin_post, "pending_path", lambda: str(tmp_path / "p.json"))
    monkeypatch.setattr(syndicate.voice_gate, "scan",
                        lambda *a, **k: {"violations": []})
    monkeypatch.setattr(syndicate.voice_gate, "format_report", lambda s: "PASS")
    monkeypatch.setattr(syndicate.notify, "send_telegram_message", lambda *a, **k: 4242)
    monkeypatch.setattr(syndicate.notify, "chat_id_for", lambda ch: str(LARS))
    from app import ablage
    monkeypatch.setattr(ablage, "ablegen", lambda *a, **k: None)
    item = {"title": "T", "link": "https://moltrust.ch/blog/t.html", "article_text": "x"}
    assert syndicate.deliver_linkedin(item, "text") is True
    entry = linkedin_post.load_pending()[syndicate.linkedin_key(item)]
    assert entry["message_id"] == 4242
