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
        # CM2: der Verfall liest den Zeitstempel der Nachricht; eine
        # Attrappe ohne `date` gilt als verfallen.
        "message": {"message_id": 42, "chat": {"id": 7},
                    "date": int(datetime.datetime.now(
                        datetime.timezone.utc).timestamp()),
                    "text": message(source)},
    }}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Armed, with the network replaced and nothing else."""
    # The reply budget is zero since 18.10.2026 (CJ1). These tests describe
    # the consumer as it ran with a budget, so they set the old one.
    monkeypatch.setattr(reply_radar, "DAILY_MAX", 8)
    calls = {"posted": [], "edits": [], "alerts": [], "sent": []}
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(reply_radar, "STATE_FILE", str(state_file))
    # These tests describe the branch as it ran until 17.10.2026. From 18.10
    # the gate refuses every post (tests/test_radar_retired.py); without this
    # the suite would start failing on that date for the right reason.
    monkeypatch.setattr(reply_radar, "reply_branch_retired", lambda now=None: False)
    monkeypatch.setenv(reply_radar.ARM_FLAG, "1")
    monkeypatch.setattr(reply_radar, "x_auth", lambda: "auth")
    monkeypatch.setattr(reply_radar, "target_ok",
                        lambda a, t: (True, "", {"id": t, "_author": "owasp",
                                                 "text": "the post said 42"}))
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

    def fake_post(text, reply_to=None, media_ids=None, auth=None, kind=None):
        # `kind` is what the post ledger records. The fake takes it so a new
        # argument at the send site fails in the module, not here.
        calls["posted"].append({"text": text, "reply_to": reply_to, "kind": kind})
        return "999"

    monkeypatch.setattr(reply_radar.x_post, "post", fake_post)
    # `maybe_detect_manual` is deliberately left real. It returns before
    # touching the network when nothing is pending, which is every consumer
    # test here — stubbing it would have hidden that.
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
    assert sent["kind"] == "reply", "the ledger would book this as something else"
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
        return True, "", {"id": t, "_author": "owasp", "text": "the post said 42"}

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


def test_both_sources_get_the_same_two_keys(wired):
    """CM2: Senden und Verwerfen, fuer Liste wie fuer Erwaehnung.

    Vorher trug ein Listenentwurf einen Intent-Link als ersten Knopf. Der
    ist weg: ein URL-Knopf verlaesst Telegram ohne Pruefung, und zwei
    Tastaturformen brauchen zwei Erklaerungen in der Meldung. Was die API
    auf einen fremden Post nicht darf, sagt jetzt die Antwort auf den Druck
    (test_the_list_draft_never_reaches_x gleich darueber).
    """
    calls, _ = wired
    tweet = {"id": "111", "_author": "owasp", "_source": "list",
             "public_metrics": {"impression_count": 9}}
    reply_radar.send_draft(1, tweet, "a draft with 42 in it", True, [], {})
    buttons = calls["sent"][-1]["markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["rr|post|111", "rr|drop|111"]
    assert all("url" not in b for b in buttons), "kein Knopf fuehrt aus Telegram"

    tweet["_source"] = "mention"
    reply_radar.send_draft(2, tweet, "a draft with 42 in it", True, [], {})
    buttons = calls["sent"][-1]["markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == ["rr|post|111", "rr|drop|111"]


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


# ── CM4: the weekly send cap holds the button and nothing else ──

def _spent_week_ledger(tmp_path):
    """A ledger whose replies this week cost more than the $0.50 cap."""
    now = datetime.datetime.now(datetime.timezone.utc)
    monday = now.date() - datetime.timedelta(days=now.date().weekday())
    rows = [{"at": f"{monday}T09:00:00+00:00", "kind": "write", "source": "reply",
             "id": f"sent{i}", "with_url": True} for i in range(3)]
    # Three replies with a link: $0.60 against a $0.50 cap.
    rows.append({"at": f"{now.date()}T06:05:00+00:00", "kind": "read",
                 "source": "tweets", "posts": ["p1", "p2"], "users": []})
    p = tmp_path / "x_meter_spent.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(p)


def test_send_cap_stops_before_the_client(wired, monkeypatch, tmp_path):
    """CM4 criterion 4: a spent week refuses the post and hands over the text."""
    calls, state_file = wired
    led = _spent_week_ledger(tmp_path)
    monkeypatch.setattr(reply_radar.x_meter, "ledger_path", lambda: led)
    monkeypatch.setattr(reply_radar.telegram_inbox, "claim",
                        lambda *a, **k: [callback("post")])
    reply_radar.consume_and_post()

    assert calls["posted"] == []
    assert read_state(state_file)["decisions"]["111"]["result"] == "send_cap"
    refusal = "\n".join(calls["edits"])
    assert "0.60" in refusal                        # what the week stands at
    next_monday = reply_radar.x_meter.next_monday()
    assert next_monday in refusal                   # when it lifts
    assert "Zum Kopieren" in refusal                # the draft is handed over
    assert "EU AI Act Article 50" in refusal        # … with its text
    assert any("Wochendeckel" in a for a in calls["alerts"])


def test_send_cap_leaves_reading_and_the_measurement_alone(wired, monkeypatch,
                                                           tmp_path):
    """CM4 criterion 5: the pause is the button, not the day's reads.

    The same ledger that closes the send path answers the read questions
    unchanged — the reading list and the CM5 measurement never ask sends_paused.
    """
    _, _ = wired
    led = _spent_week_ledger(tmp_path)
    monkeypatch.setattr(reply_radar.x_meter, "ledger_path", lambda: led)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    assert reply_radar.x_meter.sends_paused() is not None
    assert reply_radar.x_meter.reads_paused() is None
    day = reply_radar.x_meter.spend(today, led)
    # On a Monday the week's three replies fall on today as well ($0.60).
    monday = datetime.datetime.now(datetime.timezone.utc).weekday() == 0
    assert day["posts"] == 2 and day["usd"] == (0.61 if monday else 0.01)
