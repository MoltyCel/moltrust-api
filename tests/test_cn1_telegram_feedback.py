"""CN1: a Telegram reply to a draft message gives a new version of the draft.

Herkunft: ops CN1 (Freigabe Lars 03.11.2026). Lars answers a draft message
with Telegram's reply function; the text is feedback, the radar sends a new
version in the same chat with one line naming the change. A draft with
feedback stays open until it is posted (CM22) or discarded; a draft without
feedback expires after 24 hours, which replaces the twelve hours of CM2.

  K2  a reply to a draft message is matched by message id; replies to other
      messages and replies from other senders do nothing
  K3  the new version is a reply to the draft message, change line first,
      text and link as in CM16/CM17, and it goes through the voice gate again
  K4  24 h without feedback, open with feedback, the 12 h reason clock stays
  K5  one line per version in data/reply_radar_decisions.jsonl
"""
import datetime
import json

import pytest

from agents import reply_decisions as rd
from agents import reply_radar as rr

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 11, 4, 6, 5, tzinfo=UTC)
LARS = 4711                      # the private drafts chat: chat id == user id
DRAFT_MID = 900
TWEET = {"id": "2117000000000000001", "_author": "owasp", "_source": "list",
         "text": "Agents now hold 40% of API keys in the survey.",
         "public_metrics": {"impression_count": 3000, "like_count": 12}}
FIRST = "In the survey 40% of keys sit with agents; a key says who, not what it may do."


def at(hours: float) -> datetime.datetime:
    return T0 + datetime.timedelta(hours=hours)


@pytest.fixture
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(rd, "open_path", lambda: str(tmp_path / "open.json"))
    monkeypatch.setattr(rd, "decisions_path", lambda: str(tmp_path / "decisions.jsonl"))
    return tmp_path


def lines(tmp_path) -> list[dict]:
    p = tmp_path / "decisions.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def world(monkeypatch, files):
    """Telegram, the inbox, the drafter and the KB without a network."""
    seen = {"sent": [], "claims": [], "drafter": [], "inbox": []}

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

    def fake_drafter(tweet, kb, avoid=None, revise=None):
        seen["drafter"].append({"tweet": tweet, "revise": revise})
        rr._LAST_CHANGE["change"] = "Zahl aus dem Post vorangestellt"
        return ("40% of API keys in the survey sit with agents, and a key names "
                "the holder, never the scope.", [])

    monkeypatch.setattr(rr.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(rr.notify, "chat_id_for", lambda ch: str(LARS))
    monkeypatch.setattr(rr.telegram_inbox, "claim", fake_claim)
    monkeypatch.setattr(rr, "draft_reply", fake_drafter)
    monkeypatch.setattr(rr, "load_kb", lambda: {})
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    # The voice gate's rule files live on the server; here it passes, and
    # test_the_new_version_goes_through_the_voice_gate replaces it with one
    # that blocks, to show the new version is checked.
    monkeypatch.setattr(rr, "check", lambda text, sources: (True, [], {}))
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: True)
    monkeypatch.setattr(rr, "intent_link_open", lambda now=None: True)
    rd.register(TWEET, True, [], DRAFT_MID, chat_id=LARS, now=T0, text=FIRST)
    return seen


def reply(text: str, to: int = DRAFT_MID, sender: int = LARS, bot: bool = False,
          chat: int = LARS, mid: int = 950) -> dict:
    return {"update_id": mid, "message": {
        "message_id": mid, "text": text, "chat": {"id": chat, "type": "private"},
        "from": {"id": sender, "is_bot": bot},
        "reply_to_message": {"message_id": to}}}


# ── K2: zugeordnet über die Nachrichten-ID, und nur das ──

def test_a_reply_from_lars_to_the_draft_gives_a_new_version(world, files):
    world["inbox"].append(reply("Mehr auf die Zahl im Post eingehen."))
    assert rr.consume_feedback() == 1
    # The feedback reached the drafter together with the previous draft.
    [call] = world["drafter"]
    assert call["revise"] == (FIRST, "Mehr auf die Zahl im Post eingehen.")
    assert call["tweet"]["id"] == TWEET["id"]
    assert call["tweet"]["text"] == TWEET["text"]      # stored, no X read
    assert len(world["sent"]) == 1


def test_only_replies_to_draft_messages_are_claimed(world):
    world["inbox"].append(reply("Zu einer anderen Meldung.", to=12345))
    assert rr.consume_feedback() == 0
    [(consumer, kinds, kw)] = world["claims"]
    assert kinds == ["message"] and kw["reply_to"] == [DRAFT_MID]
    assert world["drafter"] == [] and world["sent"] == []
    # The other reply stays in the inbox for whoever owns it.
    assert len(world["inbox"]) == 1


def test_a_reply_from_another_sender_does_nothing(world, files):
    world["inbox"].append(reply("Ich bin nicht Lars.", sender=999))
    world["inbox"].append(reply("Ich bin ein Bot.", bot=True, mid=951))
    assert rr.consume_feedback() == 0
    assert world["drafter"] == [] and world["sent"] == []
    assert rd.open_entry(TWEET["id"]).get("feedback_at") is None
    assert lines(files) == []


def test_nothing_open_claims_nothing(monkeypatch, files):
    monkeypatch.setattr(rr.telegram_inbox, "claim",
                        lambda *a, **k: pytest.fail("claimed without an open draft"))
    assert rr.consume_feedback() == 0


# ── K3: Antwort in derselben Unterhaltung, Zeile zur Aenderung, Gate ──

def test_the_new_version_replies_to_the_draft_with_the_change_first(world):
    world["inbox"].append(reply("Mehr auf die Zahl im Post eingehen."))
    rr.consume_feedback()
    [msg] = world["sent"]
    assert msg["reply_to"] == DRAFT_MID
    first_line = msg["text"].split("\n", 1)[0]
    assert first_line == "✏️ Fassung 2: Zahl aus dem Post vorangestellt"
    body = msg["text"]
    assert "Reply-Entwurf, Fassung 2" in body
    assert f"https://x.com/owasp/status/{TWEET['id']}" in body
    assert "https://x.com/intent/post?in_reply_to=" in body        # CM16 link
    assert msg.get("reply_markup") is None                         # CM17: no key


def test_the_new_version_goes_through_the_voice_gate(world, monkeypatch):
    checked = []

    def gate(text, sources):
        checked.append(text)
        return False, ["gate 2 (f): no number"], {}

    monkeypatch.setattr(rr, "check", gate)
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    assert checked == ["40% of API keys in the survey sit with agents, and a key "
                       "names the holder, never the scope."]
    body = world["sent"][0]["text"]
    assert "BLOCKIERT" in body and "gate 2 (f): no number" in body
    assert "intent/post" not in body


def test_a_reply_to_a_later_version_continues_the_same_draft(world, files):
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    second_mid = 1001
    world["inbox"].append(reply("Noch kürzer.", to=second_mid, mid=952))
    assert rr.consume_feedback() == 1
    assert world["sent"][-1]["reply_to"] == second_mid
    assert "Fassung 3" in world["sent"][-1]["text"]
    assert world["drafter"][-1]["revise"][0].startswith("40% of API keys")


# ── K4: 24 Stunden ohne Rueckmeldung, offen mit Rueckmeldung ──

def test_a_draft_without_feedback_expires_after_24_hours(files):
    rd.register(TWEET, True, [], DRAFT_MID, chat_id=LARS, now=T0, text=FIRST)
    assert rd.due(now=at(23.9)) == []
    closed = rd.due(now=at(24.1))
    assert [(c[0], c[1]) for c in closed] == [(TWEET["id"], rd.EXPIRED)]
    assert rd.DRAFT_TTL_HOURS == 24 and rr.DRAFT_TTL_HOURS == 24


def test_a_draft_with_feedback_stays_open(world, files):
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    assert rd.due(now=at(24 * 30)) == []
    assert rd.open_entry(TWEET["id"]) is not None
    assert not rd.is_closed(TWEET["id"])


def test_a_draft_with_feedback_closes_when_posted(world, files):
    """CM22 closes a posted draft through close(..., SENT)."""
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    assert rd.close(TWEET["id"], rd.SENT, now=at(48)) is True
    assert rd.is_closed(TWEET["id"]) and rd.open_entry(TWEET["id"]) is None
    assert [r["result"] for r in lines(files) if r.get("result")] == [rd.SENT]


def test_a_draft_with_feedback_closes_when_lars_discards_it(world, files):
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    world["inbox"].append(reply("verwerfen passt nicht zum Thread", to=1001, mid=953))
    assert rr.consume_feedback() == 1
    [row] = [r for r in lines(files) if r.get("result")]
    assert row["result"] == rd.DROPPED and row["reason"] == "passt nicht zum Thread"
    assert world["sent"][-1]["reply_to"] == 1001
    assert rd.open_entry(TWEET["id"]) is None


def test_the_twelve_hour_reason_clock_stays(world, files):
    """CM6: after Verwerfen the reason question still closes after 12 h."""
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    rd.mark_asked(TWEET["id"], {}, 777, now=at(30))
    assert rd.due(now=at(30 + 11.9)) == []
    closed = rd.due(now=at(30 + 12.1))
    assert [(c[1], c[2]) for c in closed] == [(rd.DROPPED, rd.NO_REASON)]
    assert rd.REASON_TTL_HOURS == 12


# ── K5: eine Zeile je Fassung ──

def test_every_version_is_its_own_line(world, files):
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    world["inbox"].append(reply("Noch kürzer.", to=1001, mid=952))
    rr.consume_feedback()
    versions = [r for r in lines(files) if r.get("kind") == rd.VERSION_KIND]
    assert [(r["draft_id"], r["version"], r["feedback"]) for r in versions] == [
        (f"rr-{TWEET['id']}", 1, None),
        (f"rr-{TWEET['id']}", 2, "Kürzer."),
        (f"rr-{TWEET['id']}", 3, "Noch kürzer."),
    ]
    assert versions[0]["text"] == FIRST
    assert versions[1]["change"] == "Zahl aus dem Post vorangestellt"
    # A version line is not a closing line: the draft is still open, and the
    # CM5 summary counts nothing for it.
    assert not rd.is_closed(TWEET["id"])
    s = rd.summary(at(-1), at(100))
    assert sum(s["counts"].values()) == 0


def test_a_closing_line_after_versions_is_still_the_one_closing_line(world, files):
    world["inbox"].append(reply("Kürzer."))
    rr.consume_feedback()
    assert rd.close(TWEET["id"], rd.SENT, now=at(5)) is True
    assert rd.close(TWEET["id"], rd.SENT, now=at(6)) is False
    assert len([r for r in lines(files) if r.get("result")]) == 1
