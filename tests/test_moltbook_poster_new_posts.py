"""The poster writes new posts again, and a run without one says so (ops P-POST).

From 2026-10-11 to 2026-10-18 no new post of moltrust-agent appeared. Every
model draft broke the content rule (italic markup five times in seven runs) and
went straight to the fallback pool; the pool had used up its five texts, reset
itself, and sent them again. Moltbook folded each repeat into the older post and
answered with that post's id, and the log said "POSTED".

Each part has a firing test and a silence test:
  * a second draft is requested, naming what the first broke
  * a model that answers is never replaced by a canned text
  * the pool only sends a text Moltbook does not already show
  * a returned post dated before the request is NOT NEW and not counted
"""
import datetime
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Imported the way the cron runs it, with agents/ on the path, which is what
# puts `activity` within reach.
for p in (str(REPO), str(REPO / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

import moltbook_poster as mp  # noqa: E402

UTC = datetime.timezone.utc
CLEAN = "TITLE: A score of zero is a measurement\nBODY:\nNo score means nobody looked. Which one did you check?"
ITALIC = "TITLE: Trust is *hard*\nBODY:\nWe think it is *very* hard."


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def _model(monkeypatch, answers):
    """Replace the model call; record every message list it was given."""
    calls = []

    def ask(api_key, messages):
        calls.append([dict(m) for m in messages])
        return answers[len(calls) - 1]

    monkeypatch.setattr(mp, "_ask_model", ask)
    monkeypatch.setattr(mp, "load_anthropic_key", lambda: "k")
    return calls


# --- the prompt -------------------------------------------------------------

def test_the_prompt_forbids_the_markup_the_rule_discards():
    prompt = mp.POST_SYSTEM_PROMPT.lower()
    assert "plain text only" in prompt
    for word in ("asterisks", "underscores", "numbered lists", "bold"):
        assert word in prompt, word


# --- two drafts -------------------------------------------------------------

def test_a_clean_first_draft_is_used_without_a_second_call(monkeypatch):
    calls = _model(monkeypatch, [CLEAN])
    post, why = mp.generate_post("topic", [])
    assert why == "ok" and post[1] == "A score of zero is a measurement"
    assert len(calls) == 1


def test_a_broken_first_draft_gets_a_second_that_names_the_breach(monkeypatch):
    calls = _model(monkeypatch, [ITALIC, CLEAN])
    post, why = mp.generate_post("topic", [])
    assert why == "ok"
    assert len(calls) == 2
    feedback = calls[1][-1]["content"]
    assert "italic markup" in feedback


def test_two_broken_drafts_are_discarded_not_edited(monkeypatch):
    calls = _model(monkeypatch, [ITALIC, ITALIC])
    post, why = mp.generate_post("topic", [])
    assert post is None and why == "discarded"
    assert len(calls) == mp.DRAFT_ATTEMPTS


def test_no_answer_is_unavailable(monkeypatch):
    _model(monkeypatch, [None])
    assert mp.generate_post("topic", []) == (None, "unavailable")


# --- pick_post and the pool -------------------------------------------------

def _no_listing(*a, **k):
    raise AssertionError("the listing must not be read when the model answered")


def test_a_model_that_answered_is_never_replaced_by_a_canned_text(monkeypatch):
    monkeypatch.setattr(mp, "generate_post", lambda t, p: (None, "discarded"))
    monkeypatch.setattr(mp, "published_titles", _no_listing)
    assert mp.pick_post({}) == (None, "discarded")


def test_the_pool_skips_texts_moltbook_already_shows(monkeypatch):
    monkeypatch.setattr(mp, "generate_post", lambda t, p: (None, "unavailable"))
    shown = {mp._norm(p[1]) for p in mp.FALLBACK_POOL[1:]}
    monkeypatch.setattr(mp, "published_titles", lambda: shown)
    post, why = mp.pick_post({})
    assert why == "fallback" and post == mp.FALLBACK_POOL[0]


def test_a_used_up_pool_posts_nothing_instead_of_resetting(monkeypatch):
    monkeypatch.setattr(mp, "generate_post", lambda t, p: (None, "unavailable"))
    monkeypatch.setattr(mp, "published_titles",
                        lambda: {mp._norm(p[1]) for p in mp.FALLBACK_POOL})
    assert mp.pick_post({}) == (None, "pool_used_up")


def test_an_unreadable_listing_keeps_the_pool_shut(monkeypatch):
    monkeypatch.setattr(mp, "generate_post", lambda t, p: (None, "unavailable"))
    monkeypatch.setattr(mp, "published_titles", lambda: None)
    assert mp.pick_post({}) == (None, "listing_unreadable")


def test_a_listing_that_ignores_its_filter_is_unreadable(monkeypatch):
    rows = [{"author": {"name": "moltrust-agent"}, "title": "ours"},
            {"author": {"name": "someone-else"}, "title": "theirs"}]
    monkeypatch.setattr(mp.requests, "get", lambda *a, **k: FakeResponse(200, {"posts": rows}))
    assert mp.published_titles() is None


def test_the_listing_asks_for_newest_first(monkeypatch):
    seen = {}

    def get(url, headers=None, params=None, timeout=None):
        seen.update(params or {})
        return FakeResponse(200, {"posts": [{"author": {"name": "moltrust-agent"},
                                             "title": "Ours  Here"}]})

    monkeypatch.setattr(mp.requests, "get", get)
    assert mp.published_titles() == {"ours here"}
    assert seen["sort"] == "new" and seen["author"] == "moltrust-agent"


# --- create_post: a folded post is not new ----------------------------------

def _post(monkeypatch, created_at, get_payload=None):
    marked = []
    monkeypatch.setattr(mp, "MOLTBOOK_KEY", "k")
    monkeypatch.setattr(mp, "mark_active", lambda did: marked.append(did))
    post = {"id": "p1"}
    if created_at is not None:
        post["created_at"] = created_at
    monkeypatch.setattr(mp.requests, "post",
                        lambda *a, **k: FakeResponse(201, {"post": post}))
    monkeypatch.setattr(mp.requests, "get",
                        lambda *a, **k: FakeResponse(200, get_payload or {}))
    return marked


def _iso(delta):
    return (datetime.datetime.now(UTC) + delta).isoformat().replace("+00:00", "Z")


def test_a_post_dated_now_is_new(monkeypatch):
    marked = _post(monkeypatch, _iso(datetime.timedelta(seconds=-5)))
    assert mp.create_post("agents", "t", "b") == "p1"
    assert marked


def test_a_post_dated_days_back_is_not_new_and_not_counted(monkeypatch, caplog):
    marked = _post(monkeypatch, _iso(datetime.timedelta(days=-12)))
    with caplog.at_level("WARNING"):
        assert mp.create_post("agents", "t", "b") is None
    assert "NOT NEW: p1" in caplog.text
    assert "POSTED" not in caplog.text
    assert not marked


def test_the_date_comes_from_the_post_itself_when_the_answer_lacks_it(monkeypatch):
    marked = _post(monkeypatch, None,
                   get_payload={"post": {"created_at": _iso(datetime.timedelta(days=-3))}})
    assert mp.create_post("agents", "t", "b") is None
    assert not marked


def test_clock_skew_inside_the_margin_still_counts_as_new(monkeypatch):
    _post(monkeypatch, _iso(-mp.NOT_NEW_SKEW + datetime.timedelta(minutes=1)))
    assert mp.create_post("agents", "t", "b") == "p1"


# --- main: a folded post ends the run with its reason -----------------------

def _run_main(monkeypatch, tmp_path, created_at):
    monkeypatch.setattr(mp, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mp, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(mp, "pick_post", lambda state: (("agents", "t", "b"), "ok"))
    _post(monkeypatch, created_at)
    mp.main()
    return mp.load_state()


def test_a_run_whose_post_is_from_yesterday_ends_not_new_and_uncounted(monkeypatch, tmp_path, caplog):
    with caplog.at_level("INFO"):
        state = _run_main(monkeypatch, tmp_path, _iso(datetime.timedelta(days=-1)))
    assert "NOT NEW: p1 created" in caplog.text
    assert "POSTED" not in caplog.text
    assert "Run done: no post, reason not_new" in caplog.text
    assert state.get("post_count", 0) == 0
    assert not state.get("last_post_time")


def test_a_run_whose_post_is_from_now_is_counted(monkeypatch, tmp_path, caplog):
    with caplog.at_level("INFO"):
        state = _run_main(monkeypatch, tmp_path, _iso(datetime.timedelta(seconds=-5)))
    assert "POSTED to m/agents! Post ID: p1" in caplog.text
    assert "NOT NEW" not in caplog.text
    assert "Run done: no post" not in caplog.text
    assert state["post_count"] == 1
