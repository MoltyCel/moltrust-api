"""The Ambassador answers on its own threads again, and silence reports itself (ops W9).

From 2026-10-10 11:30Z to 2026-10-16 the Ambassador posted no reply for 140
hours while every run logged "Run done" and the watchdog stayed green. The
author-filtered post listing had stopped returning our posts unless it was
asked for `sort=new`: 0 posts without it, 50 with it, same call otherwise.
Nothing raised, so nothing was reported.

Two parts, each with a firing test and a silence test:
  * get_our_posts asks for sort=new and keeps only the lookback window
  * the watchdog asks Moltbook, per scheduled writer, when something appeared
"""
import datetime
from pathlib import Path

import pytest

from agents import ambassador
from agents import watchdog as wd

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[1]


def _iso(hours_ago: float) -> str:
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


# --- get_our_posts ----------------------------------------------------------

def test_our_posts_are_asked_for_newest_first(monkeypatch):
    seen = {}

    def fake_get(client, path, **params):
        seen.update(params, path=path)
        return {"posts": []}

    monkeypatch.setattr(ambassador, "moltbook_get", fake_get)
    ambassador.get_our_posts(None, now=NOW)
    assert seen["path"] == "/posts"
    assert seen["author"] == ambassador.OUR_AUTHOR
    assert seen["sort"] == "new"


def test_posts_older_than_the_lookback_are_left_alone(monkeypatch):
    days = ambassador.THREAD_LOOKBACK_DAYS
    posts = [{"id": "fresh", "created_at": _iso(24)},
             {"id": "edge", "created_at": _iso(days * 24 - 1)},
             {"id": "september", "created_at": _iso(days * 24 + 1)},
             {"id": "undated"}]
    monkeypatch.setattr(ambassador, "moltbook_get", lambda c, p, **k: {"posts": posts})
    assert [p["id"] for p in ambassador.get_our_posts(None, now=NOW)] == ["fresh", "edge"]


def test_a_failed_listing_is_no_posts_not_a_crash(monkeypatch):
    monkeypatch.setattr(ambassador, "moltbook_get", lambda c, p, **k: None)
    assert ambassador.get_our_posts(None, now=NOW) == []


# --- the Moltbook silence check ---------------------------------------------

def _feed(comment_hours=2.0, post_hours=None, strangers=False):
    """A fake Moltbook: our newest comment and each account's newest post."""
    post_hours = post_hours or {"moltrust-agent": 3.0, "moltguard_v1": 5.0}

    def get(path, params):
        if path == "/agents/me/comments":
            return {"comments": [{"created_at": _iso(comment_hours)},
                                 {"created_at": _iso(comment_hours + 30)}],
                    "has_more": False}
        if path == "/posts":
            who = params["author"]
            assert params["sort"] == "new"
            rows = [{"author": {"name": who}, "created_at": _iso(post_hours[who])},
                    {"author": {"name": who}, "created_at": _iso(post_hours[who] + 24)}]
            if strangers:
                rows.append({"author": {"name": "someone-else"}, "created_at": _iso(0.1)})
            return {"posts": rows}
        raise AssertionError(path)

    return get


def _by_surface(results):
    return {r["surface"]: r for r in results}


def test_normal_operation_is_silent():
    """Silence check: a reply two hours ago and today's posts raise nothing."""
    out = _by_surface(wd.check_moltbook_writers(NOW, get=_feed()))
    assert set(out) == {w["surface"] for w in wd.MOLTBOOK_WRITERS}
    assert all(r["ok"] and not r.get("unverifiable") for r in out.values()), out


def test_a_day_without_a_reply_fires():
    out = _by_surface(wd.check_moltbook_writers(NOW, get=_feed(comment_hours=25)))
    amb = out["Moltbook/Ambassador"]
    assert amb["ok"] is False
    assert "seit 25 h" in amb["detail"] and "max 24 h" in amb["detail"]
    assert out["Moltbook/Poster"]["ok"] and out["Moltbook/MoltGuard"]["ok"]


def test_the_140_hours_of_october_would_have_fired():
    out = _by_surface(wd.check_moltbook_writers(NOW, get=_feed(comment_hours=140)))
    assert out["Moltbook/Ambassador"]["ok"] is False


def test_a_post_that_moltbook_folded_into_an_old_one_fires():
    """The poster logged POSTED daily; Moltbook dated the post days back."""
    hours = {"moltrust-agent": 6 * 24 + 4, "moltguard_v1": 5.0}
    out = _by_surface(wd.check_moltbook_writers(NOW, get=_feed(post_hours=hours)))
    assert out["Moltbook/Poster"]["ok"] is False
    assert out["Moltbook/MoltGuard"]["ok"] is True


def test_a_listing_that_ignores_its_filter_is_unreadable_not_green():
    out = _by_surface(wd.check_moltbook_writers(NOW, get=_feed(strangers=True)))
    for surface in ("Moltbook/Poster", "Moltbook/MoltGuard"):
        assert out[surface].get("unverifiable") is True
        assert out[surface]["ok"] is False


def test_an_unreachable_moltbook_is_unmeasured_not_an_outage():
    def down(path, params):
        raise ConnectionError("moltbook down")

    out = wd.check_moltbook_writers(NOW, get=down)
    assert all(r.get("unverifiable") and not r["ok"] for r in out)


def test_every_scheduled_writer_is_watched():
    """W9 criterion 5: the other Moltbook agents, not only the Ambassador."""
    watched = {w["cron"] for w in wd.MOLTBOOK_WRITERS}
    assert watched == {"agents/ambassador.py run", "agents/moltbook_poster.py",
                       "agents/moltguard.py post-deep"}
    assert {w["account"] for w in wd.MOLTBOOK_WRITERS} == {"moltrust-agent", "moltguard_v1"}


def test_the_checked_in_crontab_runs_each_watched_writer_once_and_in_time():
    tab = (REPO / "ops" / "crontab.txt").read_text()
    assert wd.threshold_findings(tab) == []
    assert not [f for f in wd.threshold_findings(tab) if f.startswith("Moltbook/")]


def test_a_writer_dropped_from_the_crontab_is_a_threshold_finding():
    tab = (REPO / "ops" / "crontab.txt").read_text()
    stripped = "\n".join(l for l in tab.splitlines() if "agents/moltguard.py post-deep" not in l)
    assert any(f.startswith("Moltbook/MoltGuard") for f in wd.threshold_findings(stripped))


@pytest.mark.parametrize("surface", ["Moltbook/Ambassador", "Moltbook/Poster",
                                     "Moltbook/MoltGuard"])
def test_the_alert_line_names_the_account_and_the_limit(surface):
    hours = {"moltrust-agent": 50.0, "moltguard_v1": 50.0}
    out = _by_surface(wd.check_moltbook_writers(NOW, get=_feed(comment_hours=50,
                                                               post_hours=hours)))
    writer = next(w for w in wd.MOLTBOOK_WRITERS if w["surface"] == surface)
    assert writer["account"] in out[surface]["detail"]
    assert f"max {writer['max_hours']} h" in out[surface]["detail"]
