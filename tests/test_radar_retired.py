"""The reply branch ends on 2026-10-18 (Lars, BW1, decided 12.10.2026).

From that date the radar may not call the drafter, may not hand out a draft or
an intent link, and --consume may not post — including a press on a draft sent
before the date. The filter keeps running and sends a reading list. Every test
moves the clock through reply_radar._utcnow, so the suite means the same thing
on 12.10 as on 30.10.
"""
import datetime
import json
import re
from pathlib import Path

import pytest
import yaml

from agents import reply_radar as rr
from agents import watchdog

UTC = datetime.timezone.utc
REGISTER = Path(__file__).resolve().parents[1] / "config" / "expectations.yaml"
BEFORE = datetime.datetime(2026, 10, 17, 16, 5, tzinfo=UTC)
AFTER = datetime.datetime(2026, 10, 18, 6, 5, tzinfo=UTC)


def at(monkeypatch, when):
    monkeypatch.setattr(rr, "_utcnow", lambda: when)


def forbidden(name):
    def boom(*a, **k):
        raise AssertionError(f"{name} was called after the gate")
    return boom


# ── the date ──

def test_the_gate_opens_at_midnight_utc_on_the_18th():
    assert not rr.reply_branch_retired(datetime.datetime(2026, 10, 17, 23, 59, 59, tzinfo=UTC))
    assert rr.reply_branch_retired(datetime.datetime(2026, 10, 18, 0, 0, tzinfo=UTC))
    assert rr.reply_branch_retired(datetime.datetime(2027, 1, 1, tzinfo=UTC))


# ── the drafter ──

def test_no_drafter_call_after_the_gate(monkeypatch):
    at(monkeypatch, AFTER)
    monkeypatch.setattr(rr, "load_anthropic_key", forbidden("load_anthropic_key"))
    monkeypatch.setattr(rr.httpx, "post", forbidden("httpx.post"))
    assert rr.draft_reply({"id": "1", "_author": "a", "text": "x"}, {}) is None
    assert rr._LAST_REFUSAL["kind"] == "retired"
    # Not a refusal by the model, so it must not mark the post seen.
    assert not rr._refusal_is_final("retired: reply branch retired")


def test_the_drafter_still_runs_before_the_gate(monkeypatch):
    at(monkeypatch, BEFORE)
    calls = []

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"content": [{"type": "text", "text": "SKIP: other — x"}],
                    "stop_reason": "end_turn", "usage": {}}

    monkeypatch.setattr(rr, "load_anthropic_key", lambda: "k")
    monkeypatch.setattr(rr.voice_gate, "load_voice_docs",
                        lambda: {"anti_ki_sprech": "", "my_voice_en": ""})
    monkeypatch.setattr(rr, "citation_block", lambda: "")
    monkeypatch.setattr(rr.httpx, "post", lambda *a, **k: calls.append(1) or Resp())
    rr.draft_reply({"id": "1", "_author": "a", "text": "x"}, {})
    assert calls == [1]


# ── no X write from the draft side ──

def test_no_intent_link_and_no_draft_message_after_the_gate(monkeypatch):
    at(monkeypatch, AFTER)
    monkeypatch.setattr(rr.notify, "send_telegram_message", forbidden("send_telegram_message"))
    with pytest.raises(RuntimeError):
        rr.intent_link("111", "text")
    tweet = {"id": "111", "_author": "a", "_source": "list", "text": "x"}
    assert rr.send_draft(1, tweet, "a reply", True, [], {}) is None


def test_intent_link_unchanged_before_the_gate(monkeypatch):
    at(monkeypatch, BEFORE)
    assert rr.intent_link("111", "a b").startswith(rr.INTENT_URL + "?in_reply_to=111")


# ── the scheduled run ──

def post(id_, author, source, text, impressions=0, followers=None):
    return {"id": id_, "author_id": f"u{id_}", "lang": "en", "_source": source,
            "_author": author, "_author_followers": followers,
            "created_at": "2026-10-18T05:00:00Z", "text": text,
            "public_metrics": {"impression_count": impressions, "like_count": 0}}


LONG = "agents need a verifiable credential before they move money for anyone"


@pytest.fixture
def scheduled(monkeypatch, tmp_path):
    """A run with the network replaced. What may not happen raises."""
    sent = []
    monkeypatch.setattr(rr, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(rr, "HEARTBEAT_FILE", str(tmp_path / "hb.json"))
    monkeypatch.setattr(rr, "x_auth", lambda: "auth")
    monkeypatch.setattr(rr.x_meter, "reads_paused", lambda *a: None)
    monkeypatch.setattr(rr, "load_targets",
                        lambda: {"alice": {"tier": 1, "group": "builders"}})
    monkeypatch.setattr(rr, "gather", lambda *a, **k: [
        post("1", "alice", "list", LONG + " today"),
        post("2", "alice", "list", LONG + " again"),
        post("3", "bob", "search", "ERC-8004 " + LONG, impressions=420, followers=900),
        post("4", "carol", "search", "short", impressions=900, followers=900),
    ])
    monkeypatch.setattr(rr.notify, "send_telegram",
                        lambda text, **k: sent.append({"text": text, **k}) or True)
    return sent, tmp_path


def test_after_the_gate_a_run_sends_one_reading_list_and_writes_nothing(scheduled, monkeypatch):
    sent, tmp_path = scheduled
    at(monkeypatch, AFTER)
    for name in ("draft_reply", "load_kb", "send_draft", "fetch_sources",
                 "check", "remember_manual"):
        monkeypatch.setattr(rr, name, forbidden(name))
    monkeypatch.setattr(rr.x_post, "post", forbidden("x_post.post"))
    monkeypatch.setattr(rr.notify, "send_telegram_message", forbidden("send_telegram_message"))

    rr.run()

    assert len(sent) == 1, "one reading list per run"
    msg = sent[0]
    assert msg.get("reply_markup") is None, "a reading list carries no button"
    body = msg["text"]
    # Link, author, reason, impressions — for each post that passed.
    assert "https://x.com/alice/status/1" in body
    assert "https://x.com/bob/status/3" in body
    assert "@bob · 420 Impressionen" in body
    assert "Grund: Liste, Tier 1 (builders)" in body
    assert "Grund: Suche, Tier 4, 420 Impressionen ≥ 50, 900 Follower ≥ 500" in body
    assert "„ERC-8004“" in body
    # One per author; the filter still drops what it dropped before.
    assert "status/2" not in body
    assert "status/4" not in body
    # No draft, in any of the shapes a draft message had.
    for marker in ("Entwurf (", "intent", "Posten", "In X antworten", "Belege"):
        assert marker not in body, marker

    state = json.loads((tmp_path / "state.json").read_text())
    assert set(state["seen"]) == {"1", "3"}
    assert not state.get("manual_pending")
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["status"] == "ok" and "reading list: 2 posts" in hb["detail"]


def test_after_the_gate_no_candidates_is_a_declared_quiet_run(scheduled, monkeypatch):
    sent, tmp_path = scheduled
    at(monkeypatch, AFTER)
    monkeypatch.setattr(rr, "gather", lambda *a, **k: [])
    rr.run()
    assert sent == []
    hb = json.loads((tmp_path / "hb.json").read_text())
    reasons = yaml.safe_load(REGISTER.read_text())
    radar = next(p for p in reasons["pipelines"] if p["name"] == "reply_radar")
    assert re.search(radar["silence_ok"]["no_candidates"], hb["detail"])


def test_a_dry_run_after_the_gate_sends_nothing(scheduled, monkeypatch, capsys):
    sent, tmp_path = scheduled
    at(monkeypatch, AFTER)
    rr.run(dry_run=True)
    assert sent == []
    assert "Leseliste" in capsys.readouterr().out
    assert not (tmp_path / "state.json").exists()


def test_before_the_gate_the_run_still_drafts(scheduled, monkeypatch):
    sent, _ = scheduled
    at(monkeypatch, BEFORE)
    asked = []
    monkeypatch.setattr(rr, "load_kb", lambda: {})
    monkeypatch.setattr(rr, "draft_reply", lambda t, kb, avoid=None: asked.append(t["id"]))
    monkeypatch.setattr(rr, "record_refusal", lambda *a, **k: "other: x")
    monkeypatch.setattr(rr, "count_by_prompt", lambda *a, **k: None)
    rr.run()
    assert asked, "the drafter was not asked before the gate"
    assert not any("Leseliste" in s["text"] for s in sent)


# ── --consume ──

def callback(verb="post", tweet_id="111"):
    text = ("📝 Reply-Entwurf 1\nQuelle (mention): https://x.com/a/status/111\n\n"
            "Entwurf (20/280):\nERC-8004 has 3 fields.\n\n")
    return {"callback_query": {"id": "cb", "data": f"rr|{verb}|{tweet_id}",
                               "from": {"username": "lars"},
                               "message": {"message_id": 42, "chat": {"id": 7},
                                           "text": text}}}


@pytest.fixture
def consumer(monkeypatch, tmp_path):
    edits = []
    monkeypatch.setattr(rr, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv(rr.ARM_FLAG, "1")
    monkeypatch.setattr(rr, "x_auth", lambda: "auth")
    monkeypatch.setattr(rr, "edit_message", lambda c, m, t: edits.append((c, m, t)))
    monkeypatch.setattr(rr.notify, "send_telegram", lambda *a, **k: True)
    return edits, tmp_path


def test_consume_refuses_to_post_after_the_gate(consumer, monkeypatch):
    edits, tmp_path = consumer
    at(monkeypatch, AFTER)
    monkeypatch.setattr(rr.x_post, "post", forbidden("x_post.post"))
    monkeypatch.setattr(rr, "target_ok", forbidden("target_ok"))
    monkeypatch.setattr(rr, "fetch_sources", forbidden("fetch_sources"))
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [callback("post")])
    rr.consume_and_post()
    decision = json.loads((tmp_path / "state.json").read_text())["decisions"]["111"]
    assert decision["result"] == "retired"
    assert any("stillgelegt" in t for _, _, t in edits)


def test_consume_closes_pending_offers_once_and_stops_reading(consumer, monkeypatch):
    edits, tmp_path = consumer
    at(monkeypatch, AFTER)
    (tmp_path / "state.json").write_text(json.dumps({"manual_pending": {
        "111": {"text": "a draft", "message_id": 42, "chat_id": 7,
                "offered_at": "2026-10-17T16:05:00+00:00"}}}))
    monkeypatch.setattr(rr, "_get", forbidden("_get (own timeline read)"))
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [])
    rr.consume_and_post()
    # The message is edited without a keyboard, which removes the intent link.
    assert [(c, m) for c, m, _ in edits] == [(7, 42)]
    assert "a draft" not in edits[0][2]
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["manual_pending"] == {}
    edits.clear()
    rr.consume_and_post()
    assert edits == [], "the offers are closed once"


def test_consume_still_posts_a_mention_before_the_gate(consumer, monkeypatch):
    edits, tmp_path = consumer
    at(monkeypatch, BEFORE)
    posted = []
    monkeypatch.setattr(rr, "target_ok", lambda a, t: (True, "", {"id": t, "_author": "a",
                                                                    "text": "3 fields"}))
    monkeypatch.setattr(rr, "fetch_sources", lambda urls: {})
    monkeypatch.setattr(rr, "check", lambda t, s: (True, [], {}))
    monkeypatch.setattr(rr, "followers_now", lambda a: 36)
    monkeypatch.setattr(rr.x_post, "post",
                        lambda text, reply_to=None, kind=None, **k: posted.append(reply_to) or "9")
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [callback("post")])
    rr.consume_and_post()
    assert posted == ["111"]


def test_linkedin_buttons_still_reach_their_handler_after_the_gate(consumer, monkeypatch):
    _, _ = consumer
    at(monkeypatch, AFTER)
    seen = []
    monkeypatch.setattr(rr, "handle_linkedin", lambda cq, data, dry: seen.append(data))
    monkeypatch.setattr(rr.telegram_inbox, "claim", lambda *a, **k: [
        {"callback_query": {"data": "li|post|k1", "message": {}}}])
    rr.consume_and_post()
    assert seen == ["li|post|k1"]


# ── what the watchers read ──

SENT = "[2026-10-18T06:05:31] INFO: reading list: 3 posts of 9 candidates sent"
DRY = "[2026-10-18T06:05:31] INFO: reading list: 3 posts of 9 candidates  (dry run, not sent)"
DRAFT = "[2026-10-12T16:05:21] INFO:   draft 1 for 2109653075204141313 (@x, tier 1, list): PASS"
QUIET = "[2026-10-18T06:05:31] INFO: Candidates after filtering: 0"


def test_the_register_counts_a_sent_list_and_a_draft_and_nothing_else():
    spec = yaml.safe_load(REGISTER.read_text())
    radar = next(p for p in spec["pipelines"] if p["name"] == "reply_radar")
    rx = re.compile(radar["output_marker"])
    assert rx.search(SENT) and rx.search(DRAFT)
    assert not rx.search(DRY) and not rx.search(QUIET)


def test_the_deadman_takes_a_sent_list_as_delivery():
    now = datetime.datetime(2026, 10, 18, 7, 0, tzinfo=UTC)
    head = "[2026-10-18T06:05:01] INFO: REPLY RADAR — 2026-10-18 06:05 UTC"
    s = watchdog.radar_silence(now, [head, SENT])
    assert s["hours"] is not None and s["hours"] < 1
    s = watchdog.radar_silence(now, [head, DRY])
    assert s["hours"] is None


# ── One read a day, page 40, from 18.10 (Lars, BX2) ──

CRONTAB = Path(__file__).resolve().parents[1] / "ops" / "crontab.txt"


def test_list_page_is_fifty_up_to_the_gate_and_forty_after(monkeypatch):
    at(monkeypatch, BEFORE)
    assert rr.list_page() == 50
    at(monkeypatch, AFTER)
    assert rr.list_page() == 40
    # Forty posts at $0.005 stay under the $0.25 a day with room for mentions.
    assert rr.list_page() * 0.005 <= 0.20


def test_only_the_0605_radar_line_runs_without_a_date_guard():
    lines = [ln for ln in CRONTAB.read_text().splitlines()
             if "agents/reply_radar.py" in ln and "--consume" not in ln
             and not ln.lstrip().startswith("#")]
    unguarded = [ln for ln in lines if "-lt 20261018" not in ln]
    assert len(lines) == 4
    assert len(unguarded) == 1
    assert unguarded[0].startswith("5 6 * * * ")
    assert "--no-search" in unguarded[0]
    # The guarded lines must still run up to 17.10: the guard is a date test,
    # not a commented-out line.
    for ln in lines:
        if ln is not unguarded[0]:
            assert '[ "$(date -u +\\%Y\\%m\\%d)" -lt 20261018 ] && ' in ln


def test_the_register_expects_one_run_a_day_and_no_search():
    block = next(p for p in yaml.safe_load(REGISTER.read_text())["pipelines"]
                 if p["name"] == "reply_radar")
    assert block["max_silence_minutes"] >= 24 * 60
    search = next(q for q in block["quellen"] if q["name"] == "search")
    assert search["max_silence_minutes"] is None
