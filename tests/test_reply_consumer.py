"""The consumer's posting path, exercised for real against a fake X client.

A `NameError: x_post is not defined` reached production on 2026-09-22 and
killed a live approval, because the only line that touches the X client sits
behind both `--consume` and `REPLY_RADAR_ARMED`, and nothing had ever walked
through it. Every test here runs armed and lets the code reach the send site;
what is faked is the network, never the branch.

Since the same day the API path only exists for mentions — X answers a reply to
a third party with 403 on every tier — so the tests come in two halves: what
may still be posted, and what must never be.
"""
import datetime
import json

import pytest

from agents import reply_radar


def message(source: str = "mention") -> str:
    return f"""📝 Reply-Entwurf 1
Quelle ({source}): https://x.com/owasp/status/111
9 Impressionen · 0 Likes

Keynote: Capability Isn't Delegability

Entwurf (152/280):
EU AI Act Article 50 transparency applies from 2 Aug 2026, which turns
delegation into a record question anyone outside the operator can recompute.

Belege (im Lauf geholt, Gate 2 (h) geprüft):
· https://example.test/source

Freigabe postet die Reply innerhalb von fünf Minuten."""


MESSAGE_TEXT = message("mention")


def callback(verb: str, tweet_id: str = "111", source: str = "mention") -> dict:
    return {"callback_query": {
        "id": "cb1", "data": f"rr|{verb}|{tweet_id}",
        "from": {"username": "LarsOnMoltrust"},
        "message": {"message_id": 42, "chat": {"id": 7}, "text": message(source)},
    }}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Armed, with the network replaced and nothing else."""
    calls = {"posted": [], "edits": [], "alerts": [], "sent": []}
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

    def fake_send(text, **kw):
        calls["sent"].append({"text": text, "markup": kw.get("reply_markup")})
        return 4242

    monkeypatch.setattr(reply_radar.notify, "send_telegram_message", fake_send)
    monkeypatch.setattr(reply_radar.notify, "chat_id_for", lambda ch: 7)

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
    # Off by default: only the tests that are about it wire a timeline.
    monkeypatch.setattr(reply_radar, "maybe_detect_manual", lambda s, a: None)
    return calls, state_file


def recent_iso(minutes_ago: int = 30) -> str:
    """An offer made just now, whenever "now" happens to be when this runs."""
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)).isoformat()


def read_state(path):
    return json.loads(path.read_text()) if path.exists() else {}


# ── what may still be posted: mentions ──

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
    assert decision["route"] == "api"
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


# ── what must never be posted: anything but a mention ──

@pytest.mark.parametrize("source", ["list", "search"])
def test_a_third_party_post_is_never_sent_through_the_api(wired, monkeypatch, source):
    """X answers those with 403. The button does not exist; the guard still must."""
    calls, state_file = wired
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("post", source=source)])
    reply_radar.consume_and_post()

    assert calls["posted"] == [], "a list draft reached the X client"
    assert read_state(state_file)["decisions"]["111"]["result"] == "api_forbidden"


def test_the_list_draft_gets_a_link_and_the_mention_a_button(wired):
    calls, _ = wired
    tweet = {"id": "111", "_author": "owasp", "_source": "list",
             "public_metrics": {"impression_count": 9}}
    reply_radar.send_draft(1, tweet, "a draft with 42 in it", True, [], {})
    buttons = calls["sent"][-1]["markup"]["inline_keyboard"][0]
    assert buttons[0]["url"].startswith("https://x.com/intent/post?in_reply_to=111")
    assert "a+draft+with+42" in buttons[0]["url"] or "a%20draft%20with%2042" in buttons[0]["url"]
    assert "callback_data" not in buttons[0], "a URL button cannot also be a callback"
    assert buttons[1]["callback_data"] == "rr|drop|111"

    tweet["_source"] = "mention"
    reply_radar.send_draft(2, tweet, "a draft with 42 in it", True, [], {})
    buttons = calls["sent"][-1]["markup"]["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == "rr|post|111"


def test_a_blocked_draft_gets_no_buttons_at_all(wired):
    calls, _ = wired
    tweet = {"id": "111", "_author": "owasp", "_source": "list"}
    reply_radar.send_draft(1, tweet, "a draft", False, ["g2f — no number"], {})
    assert calls["sent"][-1]["markup"] is None


# ── the manual half: proving a hand-posted reply ──

def timeline(*pairs):
    """A fake GET /2/users/:id/tweets carrying (reply_id, target_id) pairs."""
    return {"data": [{"id": r, "created_at": "2026-09-22T18:00:00.000Z",
                      "referenced_tweets": [{"type": "replied_to", "id": t}]}
                     for r, t in pairs]}


def test_a_hand_posted_reply_is_detected_and_written_back(wired, monkeypatch):
    calls, state_file = wired
    state = {"manual_pending": {"111": {"text": "the draft", "author": "owasp",
                                        "message_id": 42, "chat_id": 7,
                                        "offered_at": recent_iso()}}}
    monkeypatch.setattr(reply_radar, "_get", lambda *a, **k: timeline(("999", "111")))
    found = reply_radar.detect_manual_posts(state, "auth")

    assert found == 1
    decision = state["decisions"]["111"]
    assert decision["route"] == "manual"
    assert decision["result"] == "posted"
    assert decision["reply_id"] == "999"
    assert decision["followers_at_post"] == 27
    assert state["manual_pending"] == {}, "a detected draft stops being watched"
    assert any("999" in e for e in calls["edits"]), "the message was not rewritten"


def test_an_unrelated_reply_on_our_timeline_is_ignored(wired, monkeypatch):
    _, _ = wired
    state = {"manual_pending": {"111": {"text": "t", "offered_at": recent_iso()}}}
    monkeypatch.setattr(reply_radar, "_get", lambda *a, **k: timeline(("999", "555")))
    assert reply_radar.detect_manual_posts(state, "auth") == 0
    assert "111" in state["manual_pending"]


def test_an_offer_nobody_took_stops_being_watched(wired, monkeypatch):
    _, _ = wired
    state = {"manual_pending": {"111": {"text": "t", "offered_at":
                                        "2020-01-01T00:00:00+00:00"}}}
    monkeypatch.setattr(reply_radar, "_get", lambda *a, **k: {"data": []})
    reply_radar.detect_manual_posts(state, "auth")
    assert state["manual_pending"] == {}


def test_the_timeline_is_not_read_more_than_once_a_quarter_hour(wired, monkeypatch):
    _, _ = wired
    reads = {"n": 0}

    def counting_get(*a, **k):
        reads["n"] += 1
        return {"data": []}

    monkeypatch.setattr(reply_radar, "_get", counting_get)
    state = {"manual_pending": {"111": {"text": "t", "offered_at": recent_iso()}}}
    reply_radar.maybe_detect_manual(state, "auth")
    reply_radar.maybe_detect_manual(state, "auth")
    reply_radar.maybe_detect_manual(state, "auth")
    assert reads["n"] == 1, "the consumer runs every 5 min; the timeline read must not"


def test_nothing_pending_means_no_timeline_read_at_all(wired, monkeypatch):
    _, _ = wired

    def boom(*a, **k):
        raise AssertionError("read the timeline with nothing to look for")

    monkeypatch.setattr(reply_radar, "_get", boom)
    reply_radar.maybe_detect_manual({}, "auth")


def test_the_counter_separates_the_two_routes():
    state = {
        "drafts_sent": 5,
        "decisions": {
            "1": {"verb": "post", "result": "posted", "route": "manual"},
            "2": {"verb": "post", "result": "posted", "route": "api"},
            "3": {"verb": "drop"},
        },
        "manual_pending": {"4": {}, "5": {}},
    }
    c = reply_radar.decision_counts(state)
    assert (c["posted"], c["posted_manual"], c["posted_api"]) == (2, 1, 1)
    assert c["handed_over"] == 2
    assert c["drop"] == 1
