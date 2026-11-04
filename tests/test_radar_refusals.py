"""Refusals carry a reason, and a cut-off is not a refusal (BN1, BN7, 11.10.2026)."""
import json

import pytest

from agents import reply_radar as rr


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def drafter(monkeypatch, tmp_path):
    sent = []

    def install(body, status=200):
        def post(url, headers=None, json=None, timeout=None):
            sent.append(json)
            return _Resp(body, status)
        monkeypatch.setattr(rr.httpx, "post", post)

    # The drafter as it ran until 17.10.2026; from 18.10 it is not called at
    # all (tests/test_radar_retired.py).
    monkeypatch.setattr(rr, "reply_branch_retired", lambda now=None: False)
    monkeypatch.setattr(rr, "load_anthropic_key", lambda: "k")
    monkeypatch.setattr(rr.voice_gate, "load_voice_docs",
                        lambda: {"anti_ki_sprech": "", "my_voice_en": ""})
    monkeypatch.setattr(rr, "citation_block", lambda: "")
    monkeypatch.setattr(rr, "kb_digest", lambda kb: "")
    monkeypatch.setattr(rr, "REFUSALS_FILE", str(tmp_path / "refusals.jsonl"))
    install.sent = sent
    return install


def _text(t, stop="end_turn"):
    return {"content": [{"type": "text", "text": t}], "stop_reason": stop,
            "usage": {"output_tokens": 40}}


TWEET = {"id": "1", "_author": "a", "_source": "list", "text": "x"}


def test_thinking_is_stated_and_leaves_room(drafter):
    drafter(_text("SKIP: no-source — nothing in the index"))
    rr.draft_reply(TWEET, {})
    req = drafter.sent[0]
    assert req["thinking"] == {"type": "adaptive"}
    assert req["max_tokens"] >= 4000


def test_the_one_remaining_skip_is_coded_and_asked_once(drafter):
    drafter(_text("SKIP: no-checkable-claim — nothing in the post or the index"))
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == "no-checkable-claim"
    assert rr._LAST_REFUSAL["skip"] is True
    assert len(drafter.sent) == 1


@pytest.mark.parametrize("raw,code", [
    ("SKIP: no-source — nothing in the index on this", "no-source"),
    ("SKIP: promotion — launch post", "promotion"),
    ("SKIP", "uncoded"),
    ("SKIP: made-up — whatever", "uncoded"),
])
def test_a_held_dropped_skip_is_counted_under_its_own_name(drafter, raw, code):
    """CN5: a dropped code is asked once more; a model that holds to it is
    counted as skip-held, never folded into the one allowed reason."""
    drafter(_text(raw))
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == rr.SKIP_HELD
    assert rr._LAST_REFUSAL["detail"].startswith(code + " then ")
    assert rr._LAST_REFUSAL["skip"] is True
    assert len(drafter.sent) == 2


def test_cut_off_is_truncated_not_a_refusal(drafter):
    drafter({"content": [{"type": "thinking", "thinking": "..."}],
             "stop_reason": "max_tokens", "usage": {"output_tokens": 8000}})
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == "truncated"
    assert rr._LAST_REFUSAL["skip"] is False


def test_record_refusal_writes_line_and_counts(drafter, tmp_path):
    drafter(_text("SKIP: no-checkable-claim — post states nothing to check"))
    rr.draft_reply(TWEET, {})
    state = {}
    why = rr.record_refusal(state, TWEET, "2026-10-11", {})
    assert why.startswith("no-checkable-claim:")
    assert state["refusals"]["2026-10-11"] == {"no-checkable-claim": 1}
    row = json.loads((tmp_path / "refusals.jsonl").read_text().splitlines()[0])
    assert row["tweet_id"] == "1" and row["kind"] == "no-checkable-claim"
    assert row["prompt"] == rr.PROMPT_VERSION
    assert rr._LAST_REFUSAL == {}


def test_only_model_refusals_are_final():
    assert rr._refusal_is_final("no-source: x")
    assert rr._refusal_is_final("uncoded: SKIP")
    assert rr._refusal_is_final("unparseable: {")
    assert not rr._refusal_is_final("truncated: stop_reason max_tokens")
    assert not rr._refusal_is_final("api-error: HTTP 529")


def test_a_draft_still_comes_through(drafter):
    drafter(_text('{"reply": "ERC-8004 has 3 registries.", "sources": ["https://x"]}'))
    assert rr.draft_reply(TWEET, {}) == ("ERC-8004 has 3 registries.", ["https://x"])
    assert rr._LAST_REFUSAL == {}


def test_search_is_erc8004_only_and_list_page_is_fifty():
    assert rr.SEARCH_QUERIES == ['("ERC-8004" OR "erc8004") -is:retweet lang:en']
    assert rr.LIST_PAGE == 50


def test_tokens_are_logged_per_call(drafter, caplog):
    import logging
    drafter(_text("SKIP: promotion — launch post"))
    with caplog.at_level(logging.INFO):
        rr.draft_reply(TWEET, {})
    assert any("tokens in=" in m and "out=40" in m for m in caplog.messages)
