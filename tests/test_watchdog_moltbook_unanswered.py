"""W9 per comment: a stranger's question on our own post, unanswered for a day (ops AMB-1).

check_moltbook_writers says whether the Ambassador wrote anything. It stays
green while one question waits for days because the Ambassador answered
somebody else. This check looks per comment, with a fake fetcher in place of
Moltbook: one firing case and a silence case for each way a comment is not
owed a report (answered, too young, spam, ours, outside the window).
"""
import datetime

import pytest

from agents import watchdog as wd

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
POST = "post-1"
QUESTION = ("How does the registry keep a revoked delegation from being replayed "
            "by an agent that cached the old credential?")
SPAM = "Try AUX on your next tx, get started now: https://aux.example.com/go"


def _iso(hours_ago: float) -> str:
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def foreign(cid, hours_ago, text=QUESTION, **extra):
    return {"id": cid, "content": text, "created_at": _iso(hours_ago),
            "author": {"name": "someone"}, "author_id": "a1", **extra}


def ours(cid, parent, hours_ago=1):
    return {"id": cid, "content": "An answer.", "created_at": _iso(hours_ago),
            "author": {"name": "moltrust-agent"},
            "author_id": "268d39bf-4408-485e-b194-d9b049490ef4", "parent_id": parent}


def fetcher(comments, post_age_hours=48, author="moltrust-agent"):
    asked = []

    def get(path, params):
        asked.append((path, dict(params)))
        if path == "/posts":
            return {"posts": [{"id": POST, "author": {"name": author},
                               "created_at": _iso(post_age_hours), "comment_count": 3}]}
        if path == f"/posts/{POST}/comments":
            return {"comments": comments}
        raise AssertionError(f"unexpected read {path}")
    get.asked = asked
    return get


def test_a_30h_old_unanswered_foreign_question_is_reported():
    r = wd.check_moltbook_unanswered(NOW, get=fetcher([foreign("c-old", 30)]))
    assert r["ok"] is False and not r.get("unverifiable")
    assert r["count"] == 1 and r["ids"] == ["c-old"]
    assert "c-old" in r["detail"]


def test_the_reads_are_the_ambassadors_own():
    get = fetcher([foreign("c-old", 30)])
    wd.check_moltbook_unanswered(NOW, get=get)
    assert get.asked[0] == ("/posts", {"author": "moltrust-agent", "sort": "new", "limit": 50})
    assert get.asked[1][0] == f"/posts/{POST}/comments"


def test_an_answered_comment_is_silent():
    r = wd.check_moltbook_unanswered(
        NOW, get=fetcher([foreign("c-old", 30, replies=[ours("r1", "c-old")])]))
    assert r["ok"] is True


def test_a_10h_old_comment_is_silent():
    r = wd.check_moltbook_unanswered(NOW, get=fetcher([foreign("c-new", 10)]))
    assert r["ok"] is True


def test_spam_is_silent():
    r = wd.check_moltbook_unanswered(NOW, get=fetcher([foreign("c-spam", 30, text=SPAM)]))
    assert r["ok"] is True


def test_our_own_comment_is_never_owed_a_reply():
    sister = foreign("c-ours", 30, author={"name": "moltguard_v1"},
                     author_id="70eb425c-0776-496b-825d-89cf4cd1f367")
    r = wd.check_moltbook_unanswered(NOW, get=fetcher([sister]))
    assert r["ok"] is True


def test_a_post_outside_the_ambassador_window_is_not_read():
    get = fetcher([foreign("c-old", 30)], post_age_hours=15 * 24)
    r = wd.check_moltbook_unanswered(NOW, get=get)
    assert r["ok"] is True
    assert [p for p, _ in get.asked] == ["/posts"]


def test_a_reply_to_someone_else_does_not_answer_this_comment():
    r = wd.check_moltbook_unanswered(
        NOW, get=fetcher([foreign("c-old", 30), foreign("c-two", 30, replies=[ours("r1", "c-two")])]))
    assert r["ok"] is False and r["ids"] == ["c-old"]


def test_at_most_five_ids_are_named():
    many = [foreign(f"c-{i}", 30) for i in range(7)]
    r = wd.check_moltbook_unanswered(NOW, get=fetcher(many))
    assert r["count"] == 7
    assert "c-4" in r["detail"] and "c-5" not in r["detail"] and "(+2)" in r["detail"]


def test_an_ignored_filter_is_unreadable_not_silent():
    r = wd.check_moltbook_unanswered(NOW, get=fetcher([foreign("c-old", 30)], author="stranger"))
    assert r["unverifiable"] is True and r["ok"] is False


def test_moltbook_down_is_unreadable_not_silent():
    def down(path, params):
        raise RuntimeError("down")
    r = wd.check_moltbook_unanswered(NOW, get=down)
    assert r["unverifiable"] is True


@pytest.mark.parametrize("hours, fires", [(23.5, False), (24.5, True)])
def test_the_limit_is_the_ambassadors_w9_day(hours, fires):
    assert wd.UNANSWERED_MAX_HOURS == 24
    r = wd.check_moltbook_unanswered(NOW, get=fetcher([foreign("c", hours)]))
    assert (r["ok"] is False) is fires
