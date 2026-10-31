"""CM2: one draft, one message, two keys, and twelve hours.

Herkunft: ops CM2 (Freigabe Lars 22.10.2026). Was hier festgehalten wird:
die Meldung nennt alles, was fuer die Entscheidung gebraucht wird; es gibt
genau zwei Knoepfe; und ein Druck auf Senden nach zwoelf Stunden sendet
nichts, sondern antwortet.

Jede Pruefung hat ihre Gegenprobe: der Befund loest aus, und der
Normalbetrieb schweigt.
"""

import datetime
import sys
import types

import pytest

from agents import reply_radar as rr


# ── Die Meldung ──

def _tweet():
    return {
        "id": "1800000000000000001",
        "text": "Wer prueft eigentlich, was ein Agent darf?",
        "_author": "someone",
        "_source": "mention",
        "public_metrics": {"impression_count": 4321, "like_count": 7},
    }


@pytest.fixture
def captured(monkeypatch):
    """send_draft ohne Netz: der Rumpf und die Tastatur zum Nachlesen."""
    seen = {}

    def fake_send(body, channel=None, parse_mode=None, reply_markup=None):
        seen["body"] = body
        seen["markup"] = reply_markup
        return 4711

    monkeypatch.setattr(rr.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(rr, "drafter_retired", lambda: False)
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: False)
    return seen


def test_the_message_carries_what_the_decision_needs(captured):
    rr.send_draft(1, _tweet(), "Ein Mandat, nicht ein Versprechen.", True, [],
                  {})
    body = captured["body"]
    assert "https://x.com/someone/status/1800000000000000001" in body
    assert "Autor: @someone" in body
    assert "4321 Impressionen" in body
    assert "Ein Mandat, nicht ein Versprechen." in body
    assert "Voice-Gate: bestanden" in body


def test_the_findings_go_in_word_for_word(captured):
    rr.send_draft(1, _tweet(), "Entwurf mit Befund.", False,
                  ["gate 2 (e): zwei Links im Thread"], {})
    body = captured["body"]
    assert "Voice-Gate: Befunde" in body
    assert "gate 2 (e): zwei Links im Thread" in body
    # Gegenprobe zum Test darueber: hier steht nicht "bestanden".
    assert "Voice-Gate: bestanden" not in body


def test_two_keys_and_no_third(captured):
    rr.send_draft(1, _tweet(), "Ein sauberer Entwurf.", True, [], {})
    rows = captured["markup"]["inline_keyboard"]
    assert len(rows) == 1
    keys = rows[0]
    assert [k["text"] for k in keys] == ["✅ Senden", "\U0001f5d1 Verwerfen"]
    # Kein Knopf fuehrt aus Telegram heraus: beide tragen callback_data,
    # keiner eine URL. Ein Intent-Link waere ein Sendeweg ohne Pruefung.
    assert all("callback_data" in k and "url" not in k for k in keys)


def test_a_list_draft_gets_the_same_two_keys(captured):
    tweet = _tweet() | {"_source": "list"}
    rr.send_draft(1, tweet, "Ein Entwurf auf einen fremden Post.", True, [], {})
    keys = captured["markup"]["inline_keyboard"][0]
    assert [k["text"] for k in keys] == ["✅ Senden", "\U0001f5d1 Verwerfen"]
    assert "403" in captured["body"]


def test_a_blocked_draft_carries_no_key(captured):
    rr.send_draft(1, _tweet(), "Entwurf mit Befund.", False, ["befund"], {})
    assert captured["markup"] is None


def test_a_closed_send_side_means_no_key_at_all(captured, monkeypatch):
    """CM14, nachgetragen: Entwurf an, Sendeseite zu, keine Tastatur.

    Stand am 27.10.2026 nach CM1. Die Fertigmeldung zu CM1 nennt als Beleg
    eine Datei tests/test_cm1_drafts_back.py, die PR #955 nicht enthaelt —
    gemergt wurden agents/reply_radar.py und tests/test_cm4_send_cap.py.
    Die Pruefung steht deshalb hier.
    """
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: True)
    # Der Stand 27. bis 30.10.; ab 31.10. traegt die Meldung den Link (CM16,
    # tests/test_cm16_intent_link.py).
    monkeypatch.setattr(rr, "intent_link_open", lambda: False)
    rr.send_draft(1, _tweet(), "Ein sauberer Entwurf.", True, [], {})
    assert captured["markup"] is None
    assert "Ein sauberer Entwurf." in captured["body"]
    assert "keinen" in captured["body"]


def test_the_message_names_the_twelve_hours(captured):
    rr.send_draft(1, _tweet(), "Ein sauberer Entwurf.", True, [], {})
    assert "12 Stunden" in captured["body"]


# ── Der Verfall ──

def _now(hours_after: float) -> datetime.datetime:
    return (datetime.datetime(2026, 10, 27, 6, 0, tzinfo=datetime.timezone.utc)
            + datetime.timedelta(hours=hours_after))


def _msg_at_six() -> dict:
    stamp = datetime.datetime(2026, 10, 27, 6, 0, tzinfo=datetime.timezone.utc)
    return {"date": int(stamp.timestamp()),
            "chat": {"id": -100},
            "message_id": 9,
            "text": "Reply-Entwurf 1\nQuelle (mention): x\n"}


def test_eleven_hours_is_not_expired():
    assert rr.draft_expired(_msg_at_six(), now=_now(11)) is False


def test_thirteen_hours_is_expired():
    assert rr.draft_expired(_msg_at_six(), now=_now(13)) is True


def test_a_message_without_a_date_counts_as_expired():
    assert rr.draft_expired({"chat": {"id": -100}}, now=_now(1)) is True
    assert rr.draft_expired({"date": "gestern"}, now=_now(1)) is True


def test_a_late_press_on_senden_sends_nothing(monkeypatch):
    """Kriterium 4: spaeter Druck sendet nichts und antwortet „verfallen"."""
    edits = []
    posted = []
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: False)
    monkeypatch.setattr(rr, "edit_message",
                        lambda chat, mid, text, **kw: edits.append(text))
    monkeypatch.setattr(rr, "draft_expired", lambda msg, now=None: True)
    monkeypatch.setattr(rr.x_post, "post",
                        lambda *a, **kw: posted.append(a) or {})

    decisions = {}
    rr.handle_decision({}, decisions, None, {"message": _msg_at_six()},
                       "post", "1800000000000000001",
                       datetime.date(2026, 10, 28), dry_run=False)

    assert posted == []
    assert decisions["1800000000000000001"]["result"] == "expired"
    assert "Verfallen" in edits[0]
    # Kriterium 5: festgehalten, ohne Grund-Rueckfrage. Die Antwort fragt
    # nichts und bietet keine Tastatur an.
    assert "?" not in edits[0]


def test_a_press_inside_the_window_gets_past_the_expiry_check(monkeypatch):
    """Schweigeprobe: der Verfall haelt nicht auf, was noch frisch ist."""
    edits = []
    monkeypatch.setattr(rr, "reply_branch_retired", lambda: False)
    monkeypatch.setattr(rr, "edit_message",
                        lambda chat, mid, text, **kw: edits.append(text))
    monkeypatch.setattr(rr, "draft_expired", lambda msg, now=None: False)
    monkeypatch.setattr(rr, "parse_draft", lambda text: None)

    decisions = {}
    rr.handle_decision({}, decisions, None, {"message": _msg_at_six()},
                       "post", "1800000000000000001",
                       datetime.date(2026, 10, 27), dry_run=False)

    # Weiter als der Verfall: der Lauf endet erst am unlesbaren Entwurf.
    assert decisions["1800000000000000001"]["result"] == "unparseable"
    assert not any("Verfallen" in e for e in edits)


def test_verwerfen_is_not_held_by_the_expiry(monkeypatch):
    """Verwerfen ist keine Sendung und braucht kein Fenster."""
    monkeypatch.setattr(rr, "draft_expired", lambda msg, now=None: True)
    decisions = {}
    rr.handle_decision({}, decisions, None, {"message": _msg_at_six()},
                       "drop", "1800000000000000001",
                       datetime.date(2026, 10, 28), dry_run=False)
    assert decisions["1800000000000000001"]["verb"] == "drop"
    assert "result" not in decisions["1800000000000000001"]
