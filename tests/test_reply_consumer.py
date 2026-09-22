"""The consumer's posting path, exercised for real against a fake X client.

A `NameError: x_post is not defined` reached production on 2026-09-22 and
killed a live approval, because the only line that touches the X client sits
behind both `--consume` and `REPLY_RADAR_ARMED`, and nothing had ever walked
through it. Every test here runs armed and lets the code reach the send site;
what is faked is the network, never the branch.
"""
import json

import pytest

from agents import reply_radar


MESSAGE_TEXT = """📝 Reply-Entwurf 1
Quelle (list): https://x.com/owasp/status/111
9 Impressionen · 0 Likes

Keynote: Capability Isn't Delegability

Entwurf (152/280):
EU AI Act Article 50 transparency applies from 2 Aug 2026, which turns
delegation into a record question anyone outside the operator can recompute.

Belege (im Lauf geholt, Gate 2 (h) geprüft):
· https://example.test/source

Freigabe postet die Reply innerhalb von fünf Minuten."""


def callback(verb: str, tweet_id: str = "111") -> dict:
    return {"callback_query": {
        "id": "cb1", "data": f"rr|{verb}|{tweet_id}",
        "from": {"username": "LarsOnMoltrust"},
        "message": {"message_id": 42, "chat": {"id": 7}, "text": MESSAGE_TEXT},
    }}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Armed, with the network replaced and nothing else."""
    calls = {"posted": [], "edits": [], "alerts": []}
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(reply_radar, "STATE_FILE", str(state_file))
    monkeypatch.setenv(reply_radar.ARM_FLAG, "1")
    monkeypatch.setattr(reply_radar, "x_auth", lambda: "auth")
    monkeypatch.setattr(reply_radar, "target_ok", lambda a, t: (True, ""))
    monkeypatch.setattr(reply_radar, "fetch_sources",
                        lambda urls: {u: "Article 50 applies from 2 Aug 2026" for u in urls})
    monkeypatch.setattr(reply_radar, "check", lambda t, s: (True, [], {}))
    monkeypatch.setattr(reply_radar, "edit_message",
                        lambda c, m, t: calls["edits"].append(t))
    monkeypatch.setattr(reply_radar.notify, "send_telegram",
                        lambda *a, **k: calls["alerts"].append(a[0] if a else ""))

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"public_metrics": {"followers_count": 27}}}

    monkeypatch.setattr(reply_radar.requests, "get", lambda *a, **k: FakeResponse())

    def fake_post(text, reply_to=None, media_ids=None, auth=None):
        calls["posted"].append({"text": text, "reply_to": reply_to})
        return "999"

    monkeypatch.setattr(reply_radar.x_post, "post", fake_post)
    return calls, state_file


def read_state(path):
    return json.loads(path.read_text()) if path.exists() else {}


def test_approval_reaches_the_x_client(wired, monkeypatch):
    """The regression test proper: the send site must actually be executed."""
    calls, state_file = wired
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("post")])
    reply_radar.consume_and_post()

    assert len(calls["posted"]) == 1, "the X client was never called"
    sent = calls["posted"][0]
    assert sent["reply_to"] == "111"
    assert "Article 50" in sent["text"]
    assert "Entwurf (" not in sent["text"], "the message chrome leaked into the post"

    decision = read_state(state_file)["decisions"]["111"]
    assert decision["result"] == "posted"
    assert decision["reply_id"] == "999"
    assert decision["followers_at_post"] == 27
    assert any("Gepostet" in e for e in calls["edits"])


def test_drop_posts_nothing(wired, monkeypatch):
    calls, state_file = wired
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("drop")])
    reply_radar.consume_and_post()
    assert calls["posted"] == []
    assert read_state(state_file)["decisions"]["111"]["verb"] == "drop"


def test_unarmed_stops_before_the_client(wired, monkeypatch):
    calls, state_file = wired
    monkeypatch.delenv(reply_radar.ARM_FLAG, raising=False)
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("post")])
    reply_radar.consume_and_post()
    assert calls["posted"] == []
    assert "not posted" in read_state(state_file)["decisions"]["111"]["result"]


def test_gate_block_stops_before_the_client(wired, monkeypatch):
    calls, state_file = wired
    monkeypatch.setattr(reply_radar, "check",
                        lambda t, s: (False, ["g2h — not found in any cited source"], {}))
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("post")])
    reply_radar.consume_and_post()
    assert calls["posted"] == []
    assert read_state(state_file)["decisions"]["111"]["result"] == "gate_block"


def test_daily_cap_stops_before_the_client(wired, monkeypatch):
    calls, state_file = wired
    today = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    state_file.write_text(json.dumps({"posted_per_day": {today: reply_radar.DAILY_MAX}}))
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("post")])
    reply_radar.consume_and_post()
    assert calls["posted"] == []
    assert read_state(state_file)["decisions"]["111"]["result"] == "capped"


def test_one_bad_row_does_not_take_the_batch(wired, monkeypatch):
    """A claimed row has left the queue; a failure on it must stay local."""
    calls, state_file = wired
    boom = {"n": 0}

    def sometimes_raises(a, t):
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("simulated")
        return True, ""

    monkeypatch.setattr(reply_radar, "target_ok", sometimes_raises)
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("post", "111"), callback("post", "222")])
    reply_radar.consume_and_post()

    state = read_state(state_file)
    assert state["decisions"]["111"]["result"].startswith("error:")
    assert state["decisions"]["222"]["result"] == "posted"
    assert len(calls["posted"]) == 1
    assert calls["alerts"], "a failure must be reported, not swallowed"


def test_draft_is_read_from_the_clicked_message():
    text, sources = reply_radar.parse_draft(MESSAGE_TEXT)
    assert text.startswith("EU AI Act Article 50")
    assert "Belege" not in text
    assert sources == ["https://example.test/source"]
