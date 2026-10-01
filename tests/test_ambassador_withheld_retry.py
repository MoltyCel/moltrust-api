"""A refused draft must not settle the comment it was drafted for.

`post_reply` has two ways of not posting, and they mean opposite things. The
network refusing a reply settles the comment: the parent is gone, there is
nothing left to answer. The content rule holding a draft back settles nothing —
the question is still there, and the next run can draft a cleaner answer.

Both used to end in `seen.add(cid)`, so a question whose first draft happened to
carry the word "free" was marked answered and never looked at again. It happened
twice, on 24. and 25.09.2026.

The comment gate arrived after that fix and repeated it: a draft it refused also
ended in `seen.add(cid)`. Between 27.09 and 28.09.2026 that closed the only two
relevant questions the agent had — one over the word "actually", one over a
missing digit in an answer to a question that had asked for no figure. Those
cases are here too, with the attempt ceiling that keeps an open comment from
drawing a fresh draft every half hour for good.

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
def harness(monkeypatch, tmp_path):
    """cmd_run with every outward call stubbed. Returns the mutable state.

    The gate itself is stubbed too. Without that, `run_allowance` asks Moltbook
    with an empty key, reads "unreadable", and `cmd_run` returns before it looks
    at a single comment — which is what made three of these tests pass over an
    empty run from the day the gate landed until 28.09.2026.

    `STATE_FILE` is redirected, because that unreadable path ends in
    `save_state`. A test run therefore wrote this harness's two-key state over
    the live `~/.ambassador_state.json`, which is how the running agent lost the
    comment ids it had already answered — at 09:05:39 on 27.09.2026 and again at
    11:58:33 on 28.09.2026, both times from a plain `pytest` invocation.
    """
    monkeypatch.setattr(ambassador, "STATE_FILE", tmp_path / "ambassador_state.json")
    calls = {"generated": 0, "posted": 0, "drafts": [], "comments_fetched": 0}

    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        lambda state, key: (10, "10 left today (stub)",
                                            {"mode": "ok", "pct": 0.0,
                                             "observed_pct": 0.0,
                                             "observed_sample": 10}))
    monkeypatch.setattr(ambassador.reply_radar, "load_kb", lambda: {})
    # c8 reads our own comment list once per run. Unstubbed it reaches Moltbook
    # with an empty key, which is the same silent-no-op shape that let three of
    # these tests pass over an empty run.
    monkeypatch.setattr(ambassador.comment_gate, "_our_comments",
                        lambda key: [{"content": t} for t in harness.recent])

    def _check(text, sources=None, require_number=True, comment_text="",
               recent_comments=()):
        calls.setdefault("checked", []).append(require_number)
        calls.setdefault("corpus_sizes", []).append(len(list(recent_comments)))
        verdicts = harness.verdicts
        return verdicts[min(len(calls["checked"]) - 1, len(verdicts) - 1)]
    monkeypatch.setattr(ambassador.comment_gate, "check_reply", _check)
    monkeypatch.setattr(ambassador.comment_gate, "redraft_note",
                        lambda text, recent=(): harness.note)

    monkeypatch.setattr(ambassador, "get_our_posts", lambda client: [
        {"id": POST_ID, "title": "t", "content": "c", "comment_count": 1}])

    def _comments(client, post_id):
        calls["comments_fetched"] += 1
        return list(harness.comments)
    monkeypatch.setattr(ambassador, "get_comments", _comments)

    # The guard. `cmd_run` that returns before the comment loop makes every
    # assertion below vacuously true, which is how three of these tests sat
    # green over an empty run from #488 until 28.09.2026. A test that means to
    # exercise the early return sets `harness.expect_early_return`.
    real_cmd_run = ambassador.cmd_run

    def _guarded(state):
        before = calls["comments_fetched"]
        result = real_cmd_run(state)
        if not harness.expect_early_return and calls["comments_fetched"] == before:
            raise AssertionError(
                "cmd_run returned before it read a single comment. The run "
                "asserted nothing. Check that the harness stubs "
                "comment_gate.run_allowance and reply_radar.load_kb — an "
                "unstubbed gate reads 'unreadable' and returns at once."
            )
        return result
    monkeypatch.setattr(ambassador, "cmd_run", _guarded)
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
        calls["drafts"].append(k.get("redraft_note"))
        return harness.draft
    monkeypatch.setattr(ambassador, "generate_reply", _generate)

    def _post(client, path, body):
        calls["posted"] += 1
        return harness.post_result
    monkeypatch.setattr(ambassador, "moltbook_post", _post)

    harness.calls = calls
    harness.draft = CLEAN_DRAFT
    harness.post_result = {"id": "new-comment"}
    harness.verdicts = [(True, [])]
    harness.note = ""
    harness.recent = []          # what c8 compares against, empty by default
    harness.expect_early_return = False
    harness.comments = [{"id": CID,
                         "content": "Does the anchor survive a key rotation?",
                         "author": {"name": "someone"}, "author_id": "a1"}]
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


# ── the comment gate, the second way a draft gets refused ──

BLOCKED = (False, ["g2f Substanz-Boden — no concrete number anywhere"])


def attempts(state):
    return state.get("gate_attempts", {})


def test_a_gate_block_leaves_the_comment_open(harness):
    """The gate refusing a draft says nothing about the question."""
    harness.verdicts = [BLOCKED]
    ambassador.cmd_run(harness.state)

    assert CID not in seen(harness.state), (
        "a gate-blocked draft marked the comment as seen — this is the 27.09.2026 "
        "bug, one line below the fix that exists for the same mistake"
    )
    assert harness.calls["posted"] == 0
    assert attempts(harness.state)[CID] == 1


def test_the_next_run_drafts_again_after_a_gate_block(harness):
    harness.verdicts = [BLOCKED]
    ambassador.cmd_run(harness.state)
    ambassador.cmd_run(harness.state)

    assert harness.calls["generated"] == 2, "the second run did not draft again"
    assert attempts(harness.state)[CID] == 2
    assert CID not in seen(harness.state)


def test_the_ceiling_settles_a_comment_the_gate_will_not_pass(harness):
    """Open is not forever. After GATE_MAX_ATTEMPTS the comment is given up on."""
    harness.verdicts = [BLOCKED]
    for _ in range(ambassador.comment_gate.GATE_MAX_ATTEMPTS):
        ambassador.cmd_run(harness.state)

    assert CID in seen(harness.state), "the ceiling did not settle the comment"
    assert harness.calls["generated"] == ambassador.comment_gate.GATE_MAX_ATTEMPTS
    assert CID not in attempts(harness.state), "the counter outlived the comment"

    ambassador.cmd_run(harness.state)
    assert harness.calls["generated"] == ambassador.comment_gate.GATE_MAX_ATTEMPTS, (
        "a given-up comment was drafted for again")


def test_a_passing_draft_clears_the_counter(harness):
    harness.verdicts = [BLOCKED]
    ambassador.cmd_run(harness.state)
    assert attempts(harness.state)[CID] == 1

    harness.verdicts = [(True, [])]
    ambassador.cmd_run(harness.state)

    assert harness.calls["posted"] == 1
    assert CID in seen(harness.state)
    assert CID not in attempts(harness.state)


def test_a_banned_word_earns_one_redraft_inside_the_same_run(harness):
    """g2a is a wording fault. The word is named and the draft asked for again."""
    harness.verdicts = [(False, ["g2a Wortverbote — tweet 1: actually"]), (True, [])]
    harness.note = "Your previous draft was refused: it used 'actually'"
    ambassador.cmd_run(harness.state)

    assert harness.calls["generated"] == 2, "the word ban did not earn a redraft"
    assert "'actually'" in (harness.calls["drafts"][1] or ""), (
        "the redraft was not told which word it tripped")
    assert harness.calls["posted"] == 1
    assert CID in seen(harness.state)
    assert CID not in attempts(harness.state)


def test_a_second_word_ban_settles_the_attempt(harness):
    """Two drafts is the budget per run, not an unbounded loop."""
    harness.verdicts = [(False, ["g2a Wortverbote — tweet 1: actually"])]
    harness.note = "Your previous draft was refused: it used 'actually'"
    ambassador.cmd_run(harness.state)

    assert harness.calls["generated"] == 2
    assert harness.calls["posted"] == 0
    assert attempts(harness.state)[CID] == 1, "the run cost more than one attempt"
    assert CID not in seen(harness.state)


def test_a_block_with_no_banned_word_does_not_redraft(harness):
    harness.verdicts = [BLOCKED]
    harness.note = ""
    ambassador.cmd_run(harness.state)

    assert harness.calls["generated"] == 1, "redrafted without a word to avoid"


def test_the_number_requirement_follows_the_question(harness, monkeypatch):
    """A comment asking for no figure does not put rule (f)'s digit on the reply."""
    monkeypatch.setattr(ambassador.comment_gate, "needs_number", lambda text: False)
    ambassador.cmd_run(harness.state)
    assert harness.calls["checked"] == [False]

    harness.state = {"seen_comments": {}, "replies_posted": 0}
    harness.calls["checked"] = []
    monkeypatch.setattr(ambassador.comment_gate, "needs_number", lambda text: True)
    ambassador.cmd_run(harness.state)
    assert harness.calls["checked"] == [True]


def test_one_reply_per_sender_per_run(harness):
    """Two comments from the same account in one pass earn one reply.

    On 2026-09-28 two drafts went to the same sender in a single run, answering
    two comments that were both echoes of our own post title. The 24-hour limit
    allows three, which is right across a day and wrong inside one pass.
    """
    second = "comment-2"
    harness.comments.append(
        {"id": second, "content": "Does revocation reach a cached verifier?",
         "author": {"name": "someone"}, "author_id": "a1"})
    ambassador.cmd_run(harness.state)

    assert harness.calls["posted"] == 1, "the same sender was answered twice"
    assert CID in seen(harness.state)
    assert second not in seen(harness.state), (
        "the second comment was settled rather than deferred — it never got an "
        "answer and never will")


def test_the_deferred_comment_is_answered_by_the_next_run(harness):
    second = "comment-2"
    harness.comments.append(
        {"id": second, "content": "Does revocation reach a cached verifier?",
         "author": {"name": "someone"}, "author_id": "a1"})
    ambassador.cmd_run(harness.state)
    ambassador.cmd_run(harness.state)

    assert harness.calls["posted"] == 2
    assert second in seen(harness.state)


def test_cmd_run_never_writes_the_state_file_itself(harness, monkeypatch):
    """Only `main` persists the state. `cmd_run` writing it too is how a test
    run reached the live agent's file."""
    def boom(state):
        raise AssertionError("cmd_run wrote the state file")

    monkeypatch.setattr(ambassador, "save_state", boom)
    harness.verdicts = [BLOCKED]
    ambassador.cmd_run(harness.state)

    harness.expect_early_return = True
    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        lambda state, key: (0, "0 left today (stub)",
                                            {"mode": "unreadable", "pct": None,
                                             "observed_pct": None}))
    monkeypatch.setattr(ambassador.notify, "send_telegram", lambda *a, **k: None)
    ambassador.cmd_run(harness.state)


def test_the_guard_fires_when_the_gate_is_left_unstubbed(harness, monkeypatch):
    """The CI rule itself.

    A suite that does not stub the gate gets the real `run_allowance`, which
    without a Moltbook key reads "unreadable" and returns 0 — and `cmd_run`
    returns before the first comment. Every assertion after that is vacuous.
    Reproduced here through the real code path, with only the network read
    stubbed out, exactly as the unstubbed suite met it.
    """
    monkeypatch.setattr(ambassador.comment_gate, "_our_comments",
                        lambda key, limit=100: None)
    monkeypatch.setattr(ambassador.notify, "send_telegram", lambda *a, **k: None)
    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        _real_run_allowance)

    with pytest.raises(AssertionError, match="before it read a single comment"):
        ambassador.cmd_run(harness.state)


# Bound before the fixture replaces it, so the guard test can call the genuine
# article rather than a hand-built imitation of it.
_real_run_allowance = ambassador.comment_gate.run_allowance


def test_post_reply_raises_rather_than_returning_none_when_withheld():
    """The two outcomes are distinguishable at the source, not only by logging."""
    with pytest.raises(ambassador.ReplyWithheld) as exc:
        ambassador.post_reply(_NullClient(), POST_ID, DIRTY_DRAFT, CID)
    assert "free" in str(exc.value)


# ── the per-run allowance, which the loop did not read ──

def test_the_run_allowance_stops_the_loop(harness, monkeypatch):
    """`room` was decremented and never checked, so a run wrote as many comments
    as it found candidates. Invisible with one own post and three comments, and a
    spent day's allowance in one pass once the thread surface widened."""
    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        lambda state, key: (1, "1 this run (stub)",
                                            {"mode": "ok", "pct": 0.0,
                                             "observed_pct": 0.0,
                                             "observed_sample": 10}))
    harness.comments.append(
        {"id": "comment-2", "content": "Does revocation reach a cached verifier?",
         "author": {"name": "somebody-else"}, "author_id": "a2"})

    ambassador.cmd_run(harness.state)

    assert harness.calls["posted"] == 1, "the run wrote more than its allowance"
    assert harness.calls["generated"] == 1, (
        "a draft was written for a reply that had no allowance left")
    assert CID in seen(harness.state)
    assert "comment-2" not in seen(harness.state), (
        "the comment beyond the allowance was settled instead of deferred")


def test_the_deferred_comment_is_taken_up_next_run(harness, monkeypatch):
    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        lambda state, key: (1, "1 this run (stub)",
                                            {"mode": "ok", "pct": 0.0,
                                             "observed_pct": 0.0,
                                             "observed_sample": 10}))
    harness.comments.append(
        {"id": "comment-2", "content": "Does revocation reach a cached verifier?",
         "author": {"name": "somebody-else"}, "author_id": "a2"})
    ambassador.cmd_run(harness.state)
    ambassador.cmd_run(harness.state)

    assert harness.calls["posted"] == 2
    assert "comment-2" in seen(harness.state)


# ── c8 at the run level ──

def test_the_run_hands_the_gate_our_own_recent_comments(harness):
    """c8 cannot fire on a corpus the run never passes, and on 2026-10-01 there
    was no corpus at all: the rule did not exist and a repeated opening went to
    two senders ninety-nine minutes apart."""
    harness.recent = ["An earlier comment of ours about digests and binding.",
                      "Another one, about revocation reaching a cached verifier."]
    ambassador.cmd_run(harness.state)
    sizes = harness.calls["corpus_sizes"]
    assert sizes, "check_reply was never reached"
    assert sizes[0] == 2, sizes


def test_a_reply_posted_this_run_joins_the_corpus_for_the_next_draft(harness):
    """Two replies in one run are the case the per-sender fingerprint was
    blindest to, because the senders differ. The second draft has to be measured
    against the first one, which no API read can supply — it is not published
    yet when the second draft is made."""
    harness.comments.append(
        {"id": "comment-2", "content": "Does revocation reach a cached verifier?",
         "author": {"name": "another"}, "author_id": "a2"})
    ambassador.cmd_run(harness.state)
    sizes = harness.calls["corpus_sizes"]
    assert len(sizes) >= 2, sizes
    assert sizes[1] == sizes[0] + 1, \
        f"the first reply did not join the corpus: {sizes}"


def test_c8_after_a_redraft_discards_instead_of_waiting_for_the_next_run(harness):
    """One redraft, then the comment is settled. Every other rule leaves it open
    for the attempt counter; a repeated opening does not, because a third ask
    gets the same sentence."""
    echo = (False, ["c8 Wiederholung über Absender hinweg — the opening sentence "
                    "repeats one we have already sent (1.00 of the way to it)"])
    harness.verdicts = [echo, echo]
    harness.note = "Your previous draft was refused: it opens the way …"
    ambassador.cmd_run(harness.state)
    assert harness.calls["posted"] == 0
    assert CID in harness.state["seen_comments"]["post-1"], \
        "a discarded comment must not come back next run"
    assert not harness.state.get("gate_attempts"), \
        "a discard leaves no counter behind"


def test_another_rule_after_a_redraft_still_keeps_the_comment_open(harness):
    """The counter-check to the test above: c8 is the only code that discards."""
    long = (False, ["c4 Überlänge — 200 words, the limit is 150"])
    harness.verdicts = [long, long]
    harness.note = "Your previous draft was refused: it ran to 200 words"
    ambassador.cmd_run(harness.state)
    assert harness.calls["posted"] == 0
    assert CID not in harness.state.get("seen_comments", {}).get("post-1", [])
    assert harness.state["gate_attempts"][CID] == 1
