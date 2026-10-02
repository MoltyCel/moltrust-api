"""The post being replied to is quotable; the pages it links to are not.

On 02.10 the gate blocked a reply carrying "114.09 ETH" as an unsourced claim
while the post it answered read

    🚨SlowMist TI Alert🚨
    💸 @aave v3 Loop Safe Module Loss: ~114.09 ETH

The figure was on screen and simply not in the corpus rule (h) searched.
"""
import pytest

from agents import reply_radar as rr, voice_gate


SLOWMIST = ("\U0001f6a8SlowMist TI Alert\U0001f6a8\n\n"
            "\U0001f4b8 @aave  v3 Loop Safe Module Loss: ~114.09 ETH\n\n"
            "\U0001f50d Root Cause: FlashLoopAdapter's open()/close() access "
            "control only checks ISafe(msg.sender).isModuleEnabled(address(this)), "
            "which is spoofable via a fake Safe that always returns true.")

DRAFT = ("114.09 ETH gone because the module trusted the caller's own claim "
         "about itself. Every check that runs inside the operator's own estate "
         "leaves a counterparty nothing to verify independently.")

TARGET = {"id": "2105855276536725599", "_author": "SlowMist_Team", "text": SLOWMIST}


def failing(result):
    out = set()
    for gate in ("gate1", "gate2"):
        out |= {k for k, v in result[gate].items() if v == "fail"}
    return out


def test_the_target_post_becomes_a_source():
    src = rr.target_as_source(TARGET)
    assert len(src) == 1
    key, text = next(iter(src.items()))
    assert "2105855276536725599" in key and "being answered" in key
    assert "114.09" in text


def test_an_empty_post_contributes_nothing():
    assert rr.target_as_source({"id": "1", "text": "   "}) == {}
    assert rr.target_as_source({"id": "1"}) == {}


def test_the_blocked_slowmist_draft_now_passes():
    """The fixture this change exists for."""
    before = voice_gate.scan([DRAFT], mode="reply", sources={})
    assert "g2h" in failing(before), "the fixture must start out blocked"

    after = voice_gate.scan([DRAFT], mode="reply",
                            sources=rr.target_as_source(TARGET))
    assert "g2h" not in failing(after), after["violations"]


def test_a_figure_in_no_source_is_still_blocked():
    invented = ("117.42 ETH gone because the module trusted the caller's own "
                "claim about itself, and nobody outside could check it.")
    result = voice_gate.scan([invented], mode="reply",
                             sources=rr.target_as_source(TARGET))
    assert "g2h" in failing(result), "an invented figure got through"


def test_a_figure_from_the_posts_links_is_not_a_source():
    """Only the post's own text. A post that links somewhere must not turn
    that whole page into something we may quote."""
    linking = {"id": "1", "_author": "x",
               "text": "the full write-up is at https://example.test/report"}
    src = rr.target_as_source(linking)
    assert "example.test" not in "".join(src.values()) or \
        src == {"https://x.com/x/status/1 (the post being answered)":
                "the full write-up is at https://example.test/report"}
    # The claim lives on the linked page, not in the post, so it stays blocked.
    result = voice_gate.scan(
        ["The report counts 114.09 ETH lost to a spoofable module check."],
        mode="reply", sources=src)
    assert "g2h" in failing(result)


def test_target_ok_hands_the_post_back(monkeypatch):
    """So the consumer can give rule (h) the post without a second read."""
    monkeypatch.setattr(rr, "_get", lambda a, u, p: {
        "data": {"id": "1", "created_at": "2026-10-02T02:59:57.000Z",
                 "text": SLOWMIST},
        "includes": {"users": [{"username": "SlowMist_Team"}]}})
    monkeypatch.setattr(rr, "MAX_TARGET_AGE_HOURS", 10 ** 6)
    ok, why, data = rr.target_ok("auth", "1")
    assert ok and not why
    assert data["_author"] == "SlowMist_Team" and "114.09" in data["text"]


def test_a_gone_post_returns_no_text(monkeypatch):
    monkeypatch.setattr(rr, "_get", lambda a, u, p: {})
    ok, why, data = rr.target_ok("auth", "1")
    assert not ok and data == {}
