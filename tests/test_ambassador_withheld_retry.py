"""A withheld draft must not settle the comment it was drafted for.

`post_reply` has two ways of not posting, and they mean opposite things. The
network refusing a reply settles the comment: the parent is gone, there is
nothing left to answer. The content rule holding a draft back settles nothing —
the question is still there, and the next run can draft a cleaner answer.

Both used to end in `seen.add(cid)`, so a question whose first draft happened to
carry the word "free" was marked answered and never looked at again. It happened
twice, on 24. and 25.09.2026.

The module is imported the way the cron invokes it (`agents/` on the path, as a
top-level module), because that is what puts `activity` and `moltbook_poster`
within reach.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ambassador  # noqa: E402


POST_ID = "post-1"
CID = "comment-1"
CLEAN_DRAFT = "A track record only its issuer can compute is a reputation service."
DIRTY_DRAFT = "Our free tier covers this."


@pytest.fixture
def harness(monkeypatch):
    """cmd_run with every outward call stubbed. Returns the mutable state."""
    calls = {"generated": 0, "posted": 0}

    monkeypatch.setattr(ambassador, "get_our_posts", lambda client: [
        {"id": POST_ID, "title": "t", "content": "c", "comment_count": 1}])
    monkeypatch.setattr(ambassador, "get_comments", lambda client, post_id: [
        {"id": CID, "content": "Does the anchor survive a key rotation?",
         "author": {"name": "someone"}, "author_id": "a1"}])
    monkeypatch.setattr(ambassador, "check_agent_rate_limit", lambda name: False)
    monkeypatch.setattr(ambassador, "check_reply_dedup", lambda name, text: None)
    monkeypatch.setattr(ambassador, "is_identity_question", lambda text: False)
    monkeypatch.setattr(ambassador, "is_our_account", lambda name, aid: False)
    monkeypatch.setattr(ambassador, "get_stage", lambda state, name, text: 1)
    monkeypatch.setattr(ambassador, "build_thread_context", lambda *a, **k: "")
    monkeypatch.setattr(ambassador, "record_reply", lambda *a, **k: None)
    monkeypatch.setattr(ambassador, "write_memory_entry", lambda **k: None)
    monkeypatch.setattr(ambassador, "write_log_entry", lambda *a, **k: None)
    monkeypatch.setattr(ambassador, "solve_verification", lambda *a, **k: None)
    monkeypatch.setattr(ambassador.time, "sleep", lambda s: None)
    monkeypatch.setattr(ambassador.httpx, "Client", lambda *a, **k: _NullClient())

    def _generate(*a, **k):
        calls["generated"] += 1
        return harness.draft
    monkeypatch.setattr(ambassador, "generate_reply", _generate)

    def _post(client, path, body):
        calls["posted"] += 1
        return harness.post_result
    monkeypatch.setattr(ambassador, "moltbook_post", _post)

    harness.calls = calls
    harness.draft = CLEAN_DRAFT
    harness.post_result = {"id": "new-comment"}
    harness.state = {"seen_comments": {}, "replies_posted": 0}
    return harness


class _NullClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def seen(state):
    return set(state["seen_comments"].get(POST_ID, []))


def test_withheld_draft_leaves_the_comment_unseen(harness):
    """The content rule fires. The comment must stay open for the next run."""
    harness.draft = DIRTY_DRAFT
    ambassador.cmd_run(harness.state)

    assert CID not in seen(harness.state), (
        "a withheld draft marked the comment as seen — the question is now "
        "unanswerable, which is the bug this test exists for"
    )
    assert harness.calls["posted"] == 0, "the withheld draft reached the network"
    assert harness.state["replies_posted"] == 0


def test_next_run_drafts_again_after_a_withheld_draft(harness):
    """Second run over the same comment: a fresh draft, and a clean one posts."""
    harness.draft = DIRTY_DRAFT
    ambassador.cmd_run(harness.state)
    assert harness.calls["generated"] == 1

    harness.draft = CLEAN_DRAFT
    ambassador.cmd_run(harness.state)

    assert harness.calls["generated"] == 2, "the second run did not draft again"
    assert harness.calls["posted"] == 1
    assert CID in seen(harness.state), "the posted reply did not settle the comment"
    assert harness.state["replies_posted"] == 1


def test_successful_post_still_marks_the_comment_seen(harness):
    """Unchanged behaviour: a posted reply settles the comment."""
    ambassador.cmd_run(harness.state)

    assert CID in seen(harness.state)
    assert harness.calls["posted"] == 1
    assert harness.state["replies_posted"] == 1

    ambassador.cmd_run(harness.state)
    assert harness.calls["generated"] == 1, "a settled comment was drafted for twice"


def test_network_refusal_still_marks_the_comment_seen(harness):
    """Unchanged behaviour: `404 Parent comment not found` settles the comment.

    `moltbook_post` returns None for it. The parent is gone, so redrafting would
    aim at a thread that no longer exists.
    """
    harness.post_result = None
    ambassador.cmd_run(harness.state)

    assert CID in seen(harness.state), (
        "a network refusal left the comment open — the next run would redraft "
        "against a parent that is gone"
    )
    assert harness.calls["posted"] == 1
    assert harness.state["replies_posted"] == 0

    ambassador.cmd_run(harness.state)
    assert harness.calls["generated"] == 1


def test_post_reply_raises_rather_than_returning_none_when_withheld():
    """The two outcomes are distinguishable at the source, not only by logging."""
    with pytest.raises(ambassador.ReplyWithheld) as exc:
        ambassador.post_reply(_NullClient(), POST_ID, DIRTY_DRAFT, CID)
    assert "free" in str(exc.value)
