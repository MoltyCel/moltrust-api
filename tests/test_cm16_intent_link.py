"""CM16-CM21: the draft message carries an X intent link instead of keys.

Herkunft: Lars 31.10.2026, CM16-CM21. Der Knopfweg entfaellt; die Meldung
traegt den Zielpost, den Autor, die Aufrufe, den Entwurf als Text und darunter
einen Link https://x.com/intent/post?in_reply_to=<id>&text=<Entwurf>. Kein
Knopf, Verwerfen heisst ignorieren. Die API-Seite bleibt zu.

Jede Pruefung hat ihre Gegenprobe: vor dem 31.10. gibt es keinen Link.
"""

import datetime
import html
import re
import urllib.parse

import pytest

from agents import reply_radar as rr

UTC = datetime.timezone.utc
CM16 = datetime.datetime(2026, 10, 31, 6, 5, tzinfo=UTC)
BEFORE = datetime.datetime(2026, 10, 30, 6, 5, tzinfo=UTC)
DRAFT = "Who checks what an agent may do? A mandate & a record, not a promise."


def _tweet(source="list"):
    return {
        "id": "2116321075164500438",
        "text": "Agents will need permissions.",
        "_author": "someone",
        "_source": source,
        "public_metrics": {"impression_count": 4321, "like_count": 7},
    }


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_send(body, channel=None, parse_mode=None, reply_markup=None):
        seen["body"] = body
        seen["markup"] = reply_markup
        return 7223

    monkeypatch.setattr(rr.notify, "send_telegram_message", fake_send)
    return seen


def at(monkeypatch, when):
    monkeypatch.setattr(rr, "_utcnow", lambda: when)


def _href(body):
    m = re.search(r'<a href="([^"]+)">Antwort auf X öffnen</a>', body)
    assert m, body
    return html.unescape(m.group(1))


def test_the_gate_opens_on_the_31st():
    assert not rr.intent_link_open(datetime.datetime(2026, 10, 30, 23, 59, tzinfo=UTC))
    assert rr.intent_link_open(datetime.datetime(2026, 10, 31, 0, 0, tzinfo=UTC))


def test_the_link_carries_target_and_draft(monkeypatch):
    at(monkeypatch, CM16)
    link = rr.intent_link("2116321075164500438", DRAFT)
    parts = urllib.parse.urlsplit(link)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://x.com/intent/post"
    q = urllib.parse.parse_qs(parts.query)
    assert q == {"in_reply_to": ["2116321075164500438"], "text": [DRAFT]}


@pytest.mark.parametrize("source", ["list", "search", "mention"])
def test_the_message_has_everything_and_no_key(monkeypatch, captured, source):
    at(monkeypatch, CM16)
    assert rr.send_draft(1, _tweet(source), DRAFT, True, [], {}) == 7223
    body = captured["body"]
    assert "https://x.com/someone/status/2116321075164500438" in body
    assert "Autor: @someone" in body
    assert "4321 Impressionen" in body
    assert f"<pre>{html.escape(DRAFT)}</pre>" in body
    assert _href(body) == rr.intent_link("2116321075164500438", DRAFT)
    assert "Nach einem Kontowechsel den Link erneut antippen" in body
    assert "Verwerfen heißt ignorieren." in body
    # CM17: kein Knopf, auch kein zweiter.
    assert captured["markup"] is None


def test_the_link_sits_under_the_draft(monkeypatch, captured):
    at(monkeypatch, CM16)
    rr.send_draft(1, _tweet(), DRAFT, True, [], {})
    body = captured["body"]
    assert body.index("<pre>" + html.escape(DRAFT)) < body.index('<a href="')


def test_a_blocked_draft_gets_no_link(monkeypatch, captured):
    at(monkeypatch, CM16)
    rr.send_draft(1, _tweet(), DRAFT, False, ["gate 2 (e): zwei Links"], {})
    assert "intent/post" not in captured["body"]
    assert captured["markup"] is None


def test_no_link_before_the_31st(monkeypatch, captured):
    at(monkeypatch, BEFORE)
    rr.send_draft(1, _tweet(), DRAFT, True, [], {})
    assert "intent/post" not in captured["body"]
    with pytest.raises(RuntimeError):
        rr.intent_link("1", "x")


def test_the_api_side_stays_closed(monkeypatch):
    """CM18: nothing goes through the API, the link does not change that."""
    at(monkeypatch, CM16)
    assert rr.reply_branch_retired()


def test_the_offer_is_not_withdrawn_five_minutes_later(monkeypatch, captured):
    """The 31.10 finding: a list draft went to manual_pending, and
    retire_pending_offers() rewrote the message at the next --consume run."""
    at(monkeypatch, CM16)
    state = {}
    monkeypatch.setattr(rr, "load_state", lambda: state)
    monkeypatch.setattr(rr, "save_state", lambda s: None)
    msg = rr.send_draft(1, _tweet("list"), DRAFT, True, [], {})
    assert msg == 7223
    # The run loop only calls remember_manual while the API side is open.
    assert rr.reply_branch_retired()
    assert not state.get("manual_pending")
