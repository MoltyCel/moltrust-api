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


@pytest.mark.parametrize("raw,kind", [
    ("SKIP: no-source — nothing in the index on this", "no-source"),
    ("SKIP: promotion — launch post", "promotion"),
    ("SKIP", "uncoded"),
    ("SKIP: made-up — whatever", "uncoded"),
])
def test_skip_reason_is_coded(drafter, raw, kind):
    drafter(_text(raw))
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == kind
    assert rr._LAST_REFUSAL["skip"] is True


def test_cut_off_is_truncated_not_a_refusal(drafter):
    drafter({"content": [{"type": "thinking", "thinking": "..."}],
             "stop_reason": "max_tokens", "usage": {"output_tokens": 8000}})
    assert rr.draft_reply(TWEET, {}) is None
    assert rr._LAST_REFUSAL["kind"] == "truncated"
    assert rr._LAST_REFUSAL["skip"] is False


def test_record_refusal_writes_line_and_counts(drafter, tmp_path):
    drafter(_text("SKIP: off-subject — post is about pricing"))
    rr.draft_reply(TWEET, {})
    state = {}
    why = rr.record_refusal(state, TWEET, "2026-10-11", {})
    assert why.startswith("off-subject:")
    assert state["refusals"]["2026-10-11"] == {"off-subject": 1}
    row = json.loads((tmp_path / "refusals.jsonl").read_text().splitlines()[0])
    assert row["tweet_id"] == "1" and row["kind"] == "off-subject"
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
