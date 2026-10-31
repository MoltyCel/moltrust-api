"""RR-KEEP: a draft from the drafter keeps its text after --consume.

Herkunft: ops RR-KEEP (Lars 31.10.2026). Am 31.10. um 06:05Z ging der Entwurf
rr-2116321075164500438 (@emollick, Quelle list) als Text nach Telegram,
Nachricht 7223. Um 06:10Z hat retire_pending_offers() ihn mit dem
Rueckzugstext ueberschrieben, weil es jedes Angebot in manual_pending
zurueckzog und nicht nur die von vor dem 18.10. Damit war CM14 (der Text zum
Kopieren bleibt stehen) im Betrieb verletzt. edit_message() warf dabei die
Antwort von Telegram weg, ein abgelehntes Bearbeiten sah aus wie ein
gelungenes.

Jede Pruefung hat ihre Gegenprobe: das Altangebot wird weiter zurueckgezogen.
"""
import datetime
import json

import pytest

from agents import reply_decisions as rd
from agents import reply_radar as rr

UTC = datetime.timezone.utc
RUN = datetime.datetime(2026, 10, 31, 6, 5, 1, tzinfo=UTC)
CONSUME = datetime.datetime(2026, 10, 31, 6, 10, 2, tzinfo=UTC)
DRAFT = ("A Washington Post columnist asked OpenAI's Operator to find cheap "
         "eggs in February 2025; it bought a dozen through Instacart for "
         "$31.43 without checking back first.")


@pytest.fixture
def chat(monkeypatch, tmp_path):
    """A Telegram chat that holds message texts, and a consumer around it."""
    messages = {7223: "Entwurf (139/280):\n" + DRAFT, 42: "old offer with a link"}

    def fake_edit(chat_id, message_id, text):
        messages[message_id] = text
        return True

    monkeypatch.setattr(rr, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(rd, "open_path", lambda: str(tmp_path / "open.json"))
    monkeypatch.setattr(rd, "decisions_path", lambda: str(tmp_path / "decisions.jsonl"))
    monkeypatch.setattr(rr, "x_auth", lambda: "auth")
    monkeypatch.setattr(rr, "edit_message", fake_edit)
    monkeypatch.setattr(rr.notify, "send_telegram", lambda *a, **k: True)
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [])
    monkeypatch.setattr(rr, "_utcnow", lambda: CONSUME)
    return messages, tmp_path


def pending(**entries):
    return {"manual_pending": {
        tid: {"text": DRAFT, "author": "emollick", "source": "list",
              "message_id": mid, "chat_id": "1208875027", "offered_at": when}
        for tid, (mid, when) in entries.items()}}


def test_a_list_draft_keeps_its_text_after_a_consume_run(chat):
    messages, tmp_path = chat
    (tmp_path / "state.json").write_text(json.dumps(pending(
        **{"2116321075164500438": (7223, RUN.isoformat())})))
    rr.consume_and_post()
    assert DRAFT in messages[7223], "the draft was overwritten"
    assert "zurückgezogen" not in messages[7223]


def test_an_offer_from_before_the_18th_is_still_withdrawn(chat):
    messages, tmp_path = chat
    (tmp_path / "state.json").write_text(json.dumps(pending(
        **{"111": (42, "2026-10-17T16:05:00+00:00"),
           "2116321075164500438": (7223, RUN.isoformat())})))
    rr.consume_and_post()
    assert "zurückgezogen" in messages[42]
    assert DRAFT in messages[7223]
    assert json.loads((tmp_path / "state.json").read_text())["manual_pending"] == {}


def test_a_refused_edit_keeps_the_old_offer_for_the_next_run(chat, monkeypatch):
    _, tmp_path = chat
    monkeypatch.setattr(rr, "edit_message", lambda *a: False)
    (tmp_path / "state.json").write_text(json.dumps(pending(
        **{"111": (42, "2026-10-17T16:05:00+00:00")})))
    rr.consume_and_post()
    assert "111" in json.loads((tmp_path / "state.json").read_text())["manual_pending"]


def test_an_offer_without_a_readable_date_is_left_alone():
    assert not rr.offered_before_retirement({"offered_at": None})
    assert not rr.offered_before_retirement({})
    assert rr.offered_before_retirement({"offered_at": "2026-10-17T23:59:00Z"})
    assert not rr.offered_before_retirement({"offered_at": "2026-10-18T00:00:00Z"})


# ── edit_message reads Telegram's answer ──

class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


@pytest.mark.parametrize("status,body,expected", [
    (200, {"ok": True, "result": {}}, True),
    (400, {"ok": False, "description": "message is not modified"}, False),
    (200, {"ok": False}, False),
    (502, None, False),
])
def test_edit_message_reports_what_telegram_answered(monkeypatch, status, body, expected):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(rr.httpx, "post", lambda *a, **k: Resp(status, body))
    assert rr.edit_message("1", 2, "x") is expected


def test_edit_message_without_a_token_is_not_a_success(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert rr.edit_message("1", 2, "x") is False


# ── a draft sent again carries its own end (K1) ──

def test_expires_at_holds_a_draft_open_past_twelve_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(rd, "open_path", lambda: str(tmp_path / "open.json"))
    monkeypatch.setattr(rd, "decisions_path", lambda: str(tmp_path / "decisions.jsonl"))
    (tmp_path / "open.json").write_text(json.dumps({"drafts": {
        "1": {"draft_id": "rr-1", "offered_at": RUN.isoformat(),
              "expires_at": "2026-11-01T18:05:00+00:00"},
        "2": {"draft_id": "rr-2", "offered_at": RUN.isoformat()}}}))
    closed = rd.due(now=RUN + datetime.timedelta(hours=13))
    assert [c[0] for c in closed] == ["2"]
    assert rd.due(now=datetime.datetime(2026, 11, 1, 18, 4, tzinfo=UTC)) == []
    closed = rd.due(now=datetime.datetime(2026, 11, 1, 18, 6, tzinfo=UTC))
    assert [(c[0], c[1]) for c in closed] == [("1", rd.EXPIRED)]


def test_expires_at_never_shortens_the_twelve_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(rd, "open_path", lambda: str(tmp_path / "open.json"))
    monkeypatch.setattr(rd, "decisions_path", lambda: str(tmp_path / "decisions.jsonl"))
    (tmp_path / "open.json").write_text(json.dumps({"drafts": {
        "1": {"draft_id": "rr-1", "offered_at": RUN.isoformat(),
              "expires_at": (RUN + datetime.timedelta(hours=1)).isoformat()}}}))
    assert rd.due(now=RUN + datetime.timedelta(hours=2)) == []
