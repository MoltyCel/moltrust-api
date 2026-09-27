"""The Moltbook comment gate: what stops a comment before it is written."""
import datetime

import pytest

from agents import comment_gate as cg


def iso(minutes_ago=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)).isoformat()


def comments(n, spam, minutes_ago=5):
    return [{"created_at": iso(minutes_ago), "is_spam": i < spam} for i in range(n)]


@pytest.fixture
def live(monkeypatch):
    """A state whose gate went live an hour ago."""
    return {"gate_live_at": iso(60)}


# ── the spam rate ──

def test_a_bad_rate_stops_the_run(live, monkeypatch):
    monkeypatch.setattr(cg, "_our_comments", lambda k, limit=100: comments(20, 15))
    room, why, reading = cg.run_allowance(live, "key")
    assert room == 0
    assert reading["pct"] == 75.0 and reading["mode"] == "blocked"
    assert "75.0 %" in why


def test_a_good_rate_allows_the_daily_cap(live, monkeypatch):
    monkeypatch.setattr(cg, "_our_comments", lambda k, limit=100: comments(20, 2))
    room, why, reading = cg.run_allowance(live, "key")
    assert reading["pct"] == 10.0 and reading["mode"] == "ok"
    assert room == cg.DAILY_MAX


def test_the_threshold_is_inclusive(live, monkeypatch):
    monkeypatch.setattr(cg, "_our_comments", lambda k, limit=100: comments(20, 5))
    _, _, reading = cg.run_allowance(live, "key")
    assert reading["pct"] == cg.SPAM_BLOCK_PCT and reading["mode"] == "blocked"


def test_comments_from_before_the_gate_do_not_hold_it_hostage(live, monkeypatch):
    """The 128 old spam comments must not block the gate for good."""
    old = [{"created_at": iso(60 * 48), "is_spam": True} for _ in range(100)]
    monkeypatch.setattr(cg, "_our_comments", lambda k, limit=100: old + comments(12, 1))
    room, _, reading = cg.run_allowance(live, "key")
    assert reading["sample"] == 12, "pre-gate comments were counted"
    assert room == cg.DAILY_MAX


def test_too_small_a_sample_is_a_probe_not_a_verdict(live, monkeypatch):
    monkeypatch.setattr(cg, "_our_comments", lambda k, limit=100: comments(4, 4))
    room, why, reading = cg.run_allowance(live, "key")
    assert reading["mode"] == "probe" and reading["pct"] is None
    assert room == cg.PROBE_MAX, "the probe allowance is what produces evidence"
    assert "probe" in why


def test_an_unreadable_list_stops_the_run(live, monkeypatch):
    monkeypatch.setattr(cg, "_our_comments", lambda k, limit=100: None)
    room, why, reading = cg.run_allowance(live, "key")
    assert room == 0 and reading["mode"] == "unreadable"
    assert "blind" in why


def test_the_daily_cap_counts_down(live, monkeypatch):
    monkeypatch.setattr(cg, "_our_comments", lambda k, limit=100: comments(20, 0))
    live["comments_per_day"] = {cg.today(): cg.DAILY_MAX - 1}
    assert cg.run_allowance(live, "key")[0] == 1
    live["comments_per_day"] = {cg.today(): cg.DAILY_MAX}
    room, why, _ = cg.run_allowance(live, "key")
    assert room == 0 and "daily cap" in why


def test_counting_drops_yesterday(live):
    live["comments_per_day"] = {"2020-01-01": 9}
    cg.count_comment(live)
    assert live["comments_per_day"] == {cg.today(): 1}


def test_arm_stamps_once(live):
    first = live["gate_live_at"]
    cg.arm(live)
    assert live["gate_live_at"] == first


# ── relevance ──

@pytest.mark.parametrize("text,ok", [
    ("How does agent identity survive crossing an org boundary?", True),
    ("What does your ERC-8004 registration actually prove to a verifier?", True),
    ("nice", False),
    ("This is a long comment about gardening and the weather today here", False),
])
def test_only_on_topic_comments_are_answered(text, ok):
    assert cg.worth_answering(text)[0] is ok


def test_a_reply_naming_a_product_is_blocked(monkeypatch):
    monkeypatch.setattr(cg.voice_gate, "scan",
                        lambda *a, **k: {"gate1": {}, "gate2": {}, "violations": []})
    ok, problems = cg.check_reply("MolTrust answers that in 3 calls", {})
    assert not ok and any("product" in p for p in problems)


def test_a_gate_that_cannot_load_its_rules_blocks(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("pre-send-scan.md")

    monkeypatch.setattr(cg.voice_gate, "scan", boom)
    ok, problems = cg.check_reply("a reply with 42 in it", {})
    assert not ok and "unavailable" in problems[0]


def test_voice_gate_violations_come_through(monkeypatch):
    monkeypatch.setattr(cg.voice_gate, "scan",
                        lambda *a, **k: {"gate1": {}, "gate2": {},
                                         "violations": ["g2f — no number"]})
    ok, problems = cg.check_reply("an opinion with nothing to check", {})
    assert not ok and problems == ["g2f — no number"]


def test_the_read_uses_the_endpoint_that_exists(monkeypatch):
    """The first version asked /comments?author=… and got an error back,
    so the gate refused every run for the right reason on the wrong grounds."""
    seen = {}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"comments": []}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["params"] = kw.get("params")
        return R()

    monkeypatch.setattr(cg.httpx, "get", fake_get)
    cg._our_comments("key")
    assert seen["url"].endswith("/agents/me/comments")
    assert "author" not in (seen["params"] or {})


def test_a_missing_key_is_reported_not_silently_empty(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("asked Moltbook without a key")

    monkeypatch.setattr(cg.httpx, "get", boom)
    assert cg._our_comments("") is None
