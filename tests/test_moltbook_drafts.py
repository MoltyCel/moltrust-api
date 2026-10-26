"""Moltbook reply drafts wait for a button (ops MB1).

Nothing reaches Moltbook without Senden, a blocked draft carries no Senden, a
second press does not reply twice, and every decision is visible to W9.
"""
import pytest

from agents import moltbook_drafts as md

CID = "148d962a-da6b-4eaf-a40e-34fc566888fe"


def entry(cid=CID, created="2026-10-19T09:02:25Z", draft="A reply that answers the question."):
    return {"comment_id": cid, "post_id": "p-1", "post_title": "A post",
            "author": "someone", "created_at": created,
            "comment": "How does a cached answer learn about a revocation?",
            "draft": draft}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    path = tmp_path / "moltbook_pending.json"
    monkeypatch.setattr(md, "pending_path", lambda: str(path))
    monkeypatch.setattr(md, "_edit", lambda cq, text: edits.append(text))
    edits = []
    return edits


@pytest.fixture
def gate(monkeypatch):
    """The reply gate, decided by the test: a draft containing BLOCK fails."""
    monkeypatch.setattr(md, "scan", lambda e: ["g1a Kontrapunkt"] if "BLOCK" in e["draft"] else [])


def sender():
    sent = []

    def send(text, *, channel, parse_mode=None, reply_markup=None):
        sent.append({"text": text, "markup": reply_markup})
        return True
    send.sent = sent
    return send


def buttons(markup):
    return [b["callback_data"] for b in markup["inline_keyboard"][0]]


def press(verb, cid=CID):
    return {"message": {"chat": {"id": 1}, "message_id": 2}}, f"mb|{verb}|{cid}"


def test_a_passing_draft_comes_with_senden_and_verwerfen(gate):
    send = sender()
    assert md.offer([entry()], send=send) == 1
    assert buttons(send.sent[0]["markup"]) == [f"mb|send|{CID}", f"mb|drop|{CID}"]
    assert "check_reply, reply): PASS" in send.sent[0]["text"]


def test_a_blocked_draft_is_delivered_without_senden(gate):
    send = sender()
    assert md.offer([entry(draft="BLOCK this")], send=send) == 0
    assert buttons(send.sent[0]["markup"]) == [f"mb|drop|{CID}"]
    assert "Gate blockiert" in send.sent[0]["text"]


def test_offers_go_out_oldest_comment_first(gate):
    send = sender()
    md.offer([entry(cid="b", created="2026-10-24T10:00:00Z"),
              entry(cid="a", created="2026-10-19T09:00:00Z")], send=send)
    assert [buttons(s["markup"])[0] for s in send.sent] == ["mb|send|a", "mb|send|b"]


def test_nothing_is_sent_until_the_button_is_pressed(gate):
    posted = []
    md.offer([entry()], send=sender())
    assert posted == [] and md.decided_ids() == set()
    md.handle(*press("send"), post=lambda e, cid: posted.append(cid) or {"id": "r-1"})
    assert posted == [CID]
    assert md.load_pending()[CID]["result"] == "gesendet"
    assert md.load_pending()[CID]["reply_id"] == "r-1"


def test_a_second_press_does_not_reply_twice(gate):
    posted = []
    md.offer([entry()], send=sender())
    for _ in range(2):
        md.handle(*press("send"), post=lambda e, cid: posted.append(cid) or {"id": "r-1"})
    assert posted == [CID]


def test_senden_on_a_blocked_draft_posts_nothing(gate):
    posted = []
    md.offer([entry(draft="BLOCK this")], send=sender())
    md.handle(*press("send"), post=lambda e, cid: posted.append(cid) or {"id": "r"})
    assert posted == [] and "result" not in md.load_pending()[CID]


def test_verwerfen_is_a_decision_w9_can_see(gate):
    md.offer([entry()], send=sender())
    md.handle(*press("drop"), post=lambda e, cid: pytest.fail("must not post"))
    assert md.load_pending()[CID]["result"] == "verworfen"
    assert md.decided_ids() == {CID}


def test_a_withheld_reply_stays_owed(gate, store):
    md.offer([entry()], send=sender())

    def withheld(e, cid):
        raise RuntimeError("link")
    md.handle(*press("send"), post=withheld)
    e = md.load_pending()[CID]
    assert "result" not in e and e["error"].startswith("RuntimeError")
    assert md.decided_ids() == set()
    assert store[-1].startswith("⚠️ Nicht gesendet")


def test_the_console_discards_with_a_reason_and_nothing_goes_to_lars():
    md.discard(CID, "nur Werbung")
    e = md.load_pending()[CID]
    assert e["result"] == "verworfen (Console)" and e["reason"] == "nur Werbung"
    assert md.decided_ids() == {CID}


def test_a_decided_comment_is_not_offered_again(gate):
    md.discard(CID, "nur Werbung")
    send = sender()
    assert md.offer([entry()], send=send) == 0 and send.sent == []


def test_callback_data_fits_telegrams_64_bytes():
    assert len(f"{md.PREFIX}send|{CID}".encode()) <= 64


def test_the_consumer_hands_mb_presses_to_this_module(monkeypatch):
    """The X consumer claims every callback row; an `mb|` row it skipped would
    be a press that is gone."""
    from agents import reply_radar as rr
    handled = []
    monkeypatch.setattr(rr, "load_state", lambda: {})
    monkeypatch.setattr(rr, "save_state", lambda s: None)
    monkeypatch.setattr(rr, "x_auth", lambda: None)
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: True)
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [
        {"callback_query": {"data": f"mb|send|{CID}", "message": {}}}])
    monkeypatch.setattr(md, "handle", lambda cq, data, dry_run: handled.append(data))
    rr.consume_and_post()
    assert handled == [f"mb|send|{CID}"]


def test_the_gate_is_the_ambassadors_reply_gate_with_no_link(monkeypatch):
    """Post mode wants exactly one link and post_reply refuses any, so the scan
    has to be the reply gate the Ambassador runs."""
    from agents import comment_gate
    seen = {}

    def check_reply(text, sources=None, require_number=True, comment_text="", recent_comments=()):
        seen.update(text=text, sources=sources, require_number=require_number,
                    comment_text=comment_text)
        return True, []
    monkeypatch.setattr(comment_gate, "check_reply", check_reply)
    monkeypatch.setattr(md, "_kb", lambda: {"https://moltrust.ch/developers.html": "Our page."})
    e = dict(entry(), post_text="Our post.")
    assert md.scan(e) == []
    assert seen["comment_text"] == e["comment"] and seen["text"] == e["draft"]
    assert sorted(seen["sources"].values()) == ["Our page.", "Our post."]
    assert seen["require_number"] is comment_gate.needs_number(e["comment"])
