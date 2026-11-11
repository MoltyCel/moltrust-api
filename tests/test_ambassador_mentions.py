"""Foreign threads that mention us reach the ambassador (ops AMB-1, 27.10.2026).

Every run logged "0 threads we have commented in". Measured 2026-10-27: every
comment this account wrote since 01.10. sits on one of its own posts, so the
commented-thread surface could only ever return own posts, and the account
writes no first comment in a foreign thread. The way into a foreign thread is
an invitation: a mention, or a reply to one of our comments. Both arrive in
/notifications, which nothing read.

The listing here is a recording of GET /notifications as moltrust-agent from
2026-10-27, trimmed to the fields the code reads. Nothing is posted: the network
calls are replaced, and the reply path is exercised with a stub client.
"""
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ambassador  # noqa: E402

REC = json.loads((ROOT / "tests" / "fixtures" /
                  "moltbook_notifications_20261027.json").read_text())
OWN = set(REC["own_post_ids_in_window"])
PAGE = REC["page"]
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)
# The two foreign posts that named @moltrust-agent on 25.10.2026.
STEALTHMARK = "50ebfec7-"
OPENCLAWPRIME = "0318d4a1-"


def listing(pages):
    """moltbook_get stand-in that serves `pages` in order and records params."""
    calls = []

    def get(client, path, **params):
        assert path == "/notifications"
        calls.append(params)
        return pages[len(calls) - 1] if len(calls) <= len(pages) else None
    return get, calls


# --- the reader ---------------------------------------------------------------

def test_the_recorded_mentions_in_foreign_posts_are_found(monkeypatch):
    """Trigger: the two mentions of 25.10. are inside the 14 days and foreign."""
    get, calls = listing([PAGE])
    monkeypatch.setattr(ambassador, "moltbook_get", get)
    found = ambassador.invited_threads(None, OWN, now=NOW)
    ids = sorted(t["id"][:9] for t in found)
    assert ids == [OPENCLAWPRIME, STEALTHMARK]
    for t in found:
        assert t["source"] == "mention"
        assert t["mentions"] == [t["id"]], "a mention in the post body targets the post"
    assert calls == [{"limit": 100}], "the window ended on the first page"


def test_notifications_on_our_own_posts_are_not_foreign_threads(monkeypatch):
    """Silence: the same recording without the foreign rows yields nothing."""
    own_only = dict(PAGE, notifications=[n for n in PAGE["notifications"]
                                         if n.get("relatedPostId") in OWN
                                         or not n.get("relatedPostId")])
    assert own_only["notifications"], "the recording has own-post rows"
    get, _ = listing([own_only])
    monkeypatch.setattr(ambassador, "moltbook_get", get)
    assert ambassador.invited_threads(None, OWN, now=NOW) == []


def test_a_mention_older_than_the_window_is_left_alone(monkeypatch):
    """The 06.10. and 08.10. mentions are in the recording and out of the window."""
    get, _ = listing([PAGE])
    monkeypatch.setattr(ambassador, "moltbook_get", get)
    found = {t["id"] for t in ambassador.invited_threads(None, OWN, now=NOW)}
    old = [n["relatedPostId"] for n in PAGE["notifications"]
           if n["type"] == "mention" and n["createdAt"] < "2026-10-13"]
    assert old and not (set(old) & found)


def test_a_reply_to_our_comment_in_a_foreign_thread_is_a_commented_thread(monkeypatch):
    page = {"notifications": [{"id": "n1", "type": "comment_reply",
                               "relatedPostId": "foreign-1", "relatedCommentId": "c9",
                               "createdAt": "2026-10-26T10:00:00Z"}],
            "has_more": False}
    get, _ = listing([page])
    monkeypatch.setattr(ambassador, "moltbook_get", get)
    assert ambassador.invited_threads(None, OWN, now=NOW) == [
        {"id": "foreign-1", "comment_count": None, "source": "commented", "mentions": []}]


def test_a_mention_in_a_comment_targets_that_comment(monkeypatch):
    page = {"notifications": [{"id": "n1", "type": "mention",
                               "relatedPostId": "foreign-2", "relatedCommentId": "c7",
                               "createdAt": "2026-10-26T10:00:00Z"}],
            "has_more": False}
    get, _ = listing([page])
    monkeypatch.setattr(ambassador, "moltbook_get", get)
    assert ambassador.invited_threads(None, OWN, now=NOW)[0]["mentions"] == ["c7"]


def test_the_listing_is_read_to_the_end_of_the_window(monkeypatch):
    fresh = {"notifications": [{"id": "a", "type": "new_follower",
                                "createdAt": "2026-10-26T10:00:00Z"}],
             "has_more": True, "next_cursor": "CUR"}
    get, calls = listing([fresh, PAGE])
    monkeypatch.setattr(ambassador, "moltbook_get", get)
    assert len(ambassador.invited_threads(None, OWN, now=NOW)) == 2
    assert calls == [{"limit": 100}, {"limit": 100, "cursor": "CUR"}]


@pytest.mark.parametrize("pages", [
    [None],                                                        # unreadable
    [{"error": "x"}],                                              # wrong shape
    [{"notifications": [], "has_more": True}],                     # more, no cursor
    [{"notifications": [{"type": "mention", "relatedPostId": "f",
                         "createdAt": "2026-10-26T10:00:00Z"}],
      "has_more": True, "next_cursor": "c"}] * 20,                 # never ends
])
def test_an_incomplete_read_is_none_not_part(monkeypatch, pages):
    get, _ = listing(pages)
    monkeypatch.setattr(ambassador, "moltbook_get", get)
    assert ambassador.invited_threads(None, OWN, now=NOW) is None


def test_the_excerpt_is_the_paragraph_that_names_us():
    body = ("Roundup of the hour.\n\n1. Something about @vina.\n\n"
            "3. In m/security, `@moltrust-agent` and @agenthaven discuss key logs.\n\n"
            "4. Closing.")
    assert ambassador.mention_excerpt(body).startswith("3. In m/security")
    assert ambassador.mention_excerpt("No name here.") == "No name here."


# --- the surface and the log line ------------------------------------------------

def test_the_surface_adds_foreign_mentions_after_our_threads(monkeypatch):
    monkeypatch.setattr(ambassador, "get_our_posts", lambda c: [
        {"id": "own-1", "comment_count": 2}])
    monkeypatch.setattr(ambassador, "threads_we_are_in", lambda c: [
        {"id": "own-1", "comment_count": None, "source": "commented"}])
    monkeypatch.setattr(ambassador, "invited_threads", lambda c, own, now=None: [
        {"id": "foreign-1", "comment_count": None, "source": "mention",
         "mentions": ["foreign-1"]}])
    surface = ambassador.read_surface(None)
    assert [(p["id"], p["source"]) for p in surface] == [
        ("own-1", "own"), ("foreign-1", "mention")]


def test_an_unreadable_listing_leaves_the_rest_of_the_surface(monkeypatch):
    monkeypatch.setattr(ambassador, "get_our_posts", lambda c: [
        {"id": "own-1", "comment_count": 2}])
    monkeypatch.setattr(ambassador, "threads_we_are_in", lambda c: [])
    monkeypatch.setattr(ambassador, "invited_threads", lambda c, own, now=None: None)
    assert [p["id"] for p in ambassador.read_surface(None)] == ["own-1"]


# --- the reply path, nothing sent -------------------------------------------------

FOREIGN = "foreign-post"
POST_BODY = ("Hourly roundup.\n\n2. Treasury agents and refunds, @vina.\n\n"
             "3. In m/security, @moltrust-agent argues that a rotated signing key "
             "should invalidate inherited trust decisions even when the old "
             "signature still verifies. Does that hold for delegated credentials?")


class _Client:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def run(monkeypatch, tmp_path):
    """cmd_run over one foreign post that mentions us; every outward call stubbed."""
    monkeypatch.setattr(ambassador, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(ambassador, "DROPPED_FILE", tmp_path / "dropped.jsonl")
    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        lambda state, key: (2, "2 this run (stub)", {"mode": "ok"}))
    monkeypatch.setattr(ambassador.comment_gate, "_our_comments", lambda key: [])
    monkeypatch.setattr(ambassador.comment_gate, "check_reply",
                        lambda *a, **k: (True, []))
    monkeypatch.setattr(ambassador.reply_radar, "load_kb", lambda: {})
    monkeypatch.setattr(ambassador, "read_surface", lambda c: [
        {"id": FOREIGN, "comment_count": None, "source": "mention",
         "mentions": [FOREIGN]}])
    monkeypatch.setattr(ambassador, "get_post", lambda c, pid: {
        "id": pid, "title": "Hourly roundup", "content": POST_BODY,
        "author": {"id": "u-stealth", "name": "stealthmark"}, "is_deleted": False})
    sent = []
    drafts = []
    monkeypatch.setattr(ambassador, "get_comments", lambda c, pid: list(run.thread))
    monkeypatch.setattr(ambassador, "moltbook_post",
                        lambda c, path, body: sent.append((path, body)) or {"id": "x"})

    def gen(title, content, author, comment_text, stage, **k):
        drafts.append((author, comment_text, k.get("thread_context")))
        return "A rotated key ends the inherited decision; the old signature stays valid."
    monkeypatch.setattr(ambassador, "generate_reply", gen)
    for name, value in {"check_agent_rate_limit": lambda n: False,
                        "check_reply_dedup": lambda n, t: None,
                        "is_identity_question": lambda t: False,
                        "get_stage": lambda s, n, t: 1,
                        "record_reply": lambda *a, **k: None,
                        "write_memory_entry": lambda **k: None,
                        "write_log_entry": lambda *a, **k: None,
                        "solve_verification": lambda *a, **k: None}.items():
        monkeypatch.setattr(ambassador, name, value)
    monkeypatch.setattr(ambassador.time, "sleep", lambda s: None)
    monkeypatch.setattr(ambassador.httpx, "Client", lambda *a, **k: _Client())
    run.thread = []
    run.sent = sent
    run.drafts = drafts
    run.state = {"seen_comments": {}, "replies_posted": 0}
    return run


def test_a_post_that_mentions_us_gets_one_top_level_reply(run):
    ambassador.cmd_run(run.state)
    assert len(run.sent) == 1
    path, body = run.sent[0]
    assert path == f"/posts/{FOREIGN}/comments"
    assert "parent_id" not in body, "a reply to the post itself has no parent"
    author, asked, context = run.drafts[0]
    assert author == "stealthmark"
    assert asked.startswith("3. In m/security, @moltrust-agent"), "the excerpt is answered"
    assert context == ""
    assert FOREIGN in run.state["seen_comments"][FOREIGN]

    ambassador.cmd_run(run.state)
    assert len(run.sent) == 1, "a settled mention was answered twice"


def test_the_rest_of_a_foreign_thread_is_not_ours_to_answer(run):
    run.thread = [{"id": "c-other", "content": "Key rotation and delegated "
                   "credentials: who revokes the inherited decision, and when?",
                   "author": {"name": "vina"}, "author_id": "u-vina"}]
    ambassador.cmd_run(run.state)
    assert [d[0] for d in run.drafts] == ["stealthmark"]


def test_a_post_we_already_answered_is_not_answered_again(run):
    run.thread = [{"id": "ours", "parent_id": None, "content": "earlier",
                   "author": {"name": "moltrust-agent"}, "author_id": ""}]
    ambassador.cmd_run(run.state)
    assert run.sent == [] and run.drafts == []


def test_a_deleted_post_is_settled_without_a_draft(run, monkeypatch):
    monkeypatch.setattr(ambassador, "get_post", lambda c, pid: {
        "id": pid, "title": "Deleted post", "content": "", "is_deleted": True})
    ambassador.cmd_run(run.state)
    assert run.sent == [] and run.drafts == []
    assert FOREIGN in run.state["seen_comments"][FOREIGN]


def test_a_reply_to_a_comment_still_carries_its_parent(monkeypatch):
    sent = []
    monkeypatch.setattr(ambassador, "moltbook_post",
                        lambda c, path, body: sent.append(body) or {"id": "x"})
    monkeypatch.setattr(ambassador, "solve_verification", lambda *a: None)
    ambassador.post_reply(None, "p", "A plain sentence about key rotation.", "c1")
    assert sent[0]["parent_id"] == "c1"


# --- the guard: W9 reads mentions too ------------------------------------------------

from agents import watchdog as wd  # noqa: E402

W9_NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)
STEALTH_ID = next(n["relatedPostId"] for n in PAGE["notifications"]
                  if n["relatedPostId"] and n["relatedPostId"].startswith(STEALTHMARK))
OPENCLAW_ID = next(n["relatedPostId"] for n in PAGE["notifications"]
                   if n["relatedPostId"] and n["relatedPostId"].startswith(OPENCLAWPRIME))


@pytest.fixture
def no_decisions(tmp_path, monkeypatch):
    from agents import moltbook_drafts
    monkeypatch.setattr(moltbook_drafts, "pending_path",
                        lambda: str(tmp_path / "moltbook_pending.json"))


def w9_fetcher(stealth_comments):
    """Moltbook as the recording saw it on 27.10.: no own post in play, the
    stealthmark roundup naming us, the openclawprime post deleted."""
    def get(path, params):
        if path == "/posts":
            return {"posts": []}
        if path == "/notifications":
            return PAGE
        if path == f"/posts/{STEALTH_ID}":
            return {"post": {"id": STEALTH_ID, "content": POST_BODY,
                             "author": {"name": "stealthmark"}}}
        if path == f"/posts/{OPENCLAW_ID}":
            return {"post": {"id": OPENCLAW_ID, "content": "", "is_deleted": True,
                             "author": {"name": "openclawprime"}}}
        if path == f"/posts/{STEALTH_ID}/comments":
            return {"comments": stealth_comments}
        if path == f"/posts/{OPENCLAW_ID}/comments":
            return {"comments": []}
        raise AssertionError(f"unexpected read {path}")
    return get


def test_w9_names_an_unanswered_mention(no_decisions):
    """Trigger: the roundup of 25.10. waits 46 h with no word from us."""
    r = wd.check_moltbook_unanswered(W9_NOW, get=w9_fetcher([]))
    assert r["ok"] is False
    assert r["ids"] == [STEALTH_ID], "the deleted post owes nothing"
    assert "1 Erwähnungen in fremden Threads" in r["detail"]


def test_w9_is_green_once_we_answered_under_the_post(no_decisions):
    """Silence: our top-level comment under the post settles the mention."""
    answer = {"id": "r1", "parent_id": None, "content": "An answer.",
              "author": {"name": "moltrust-agent"}, "author_id": ""}
    r = wd.check_moltbook_unanswered(W9_NOW, get=w9_fetcher([answer]))
    assert r["ok"] is True


def test_w9_reads_an_incomplete_listing_as_unreadable(no_decisions):
    def get(path, params):
        if path == "/posts":
            return {"posts": []}
        return {"notifications": [], "has_more": True}
    r = wd.check_moltbook_unanswered(W9_NOW, get=get)
    assert r.get("unverifiable") is True
