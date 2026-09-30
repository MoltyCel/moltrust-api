"""Evergreen syndication: which archive post goes out, and what it may say."""
import datetime
import json

import pytest

from agents import syndicate as sy


NOW = datetime.datetime(2026, 9, 30, 9, 0, tzinfo=datetime.timezone.utc)


def item(link, title="T"):
    return {"link": link, "title": title, "guid": link, "category": "Analysis",
            "description": "d", "pub_date": "Sun, 13 Sep 2026 00:00:00 GMT"}


FEED = [item("https://moltrust.ch/blog/c.html"),      # newest first, as the feed is
        item("https://moltrust.ch/blog/b.html"),
        item("https://moltrust.ch/blog/a.html")]


def test_a_post_never_syndicated_is_due():
    due = sy.evergreen_candidates(FEED, {}, NOW)
    assert len(due) == 3


def test_the_oldest_due_post_goes_first():
    """The point is the posts the timeline never saw, not the newest three."""
    assert sy.evergreen_candidates(FEED, {}, NOW)[0]["link"].endswith("a.html")


def test_a_post_syndicated_last_week_is_not_due():
    reg = {"https://moltrust.ch/blog/a.html":
           {"last": (NOW - datetime.timedelta(days=7)).isoformat()}}
    links = [i["link"] for i in sy.evergreen_candidates(FEED, reg, NOW)]
    assert "https://moltrust.ch/blog/a.html" not in links


def test_ninety_days_is_the_line():
    old = {"last": (NOW - datetime.timedelta(days=91)).isoformat()}
    fresh = {"last": (NOW - datetime.timedelta(days=89)).isoformat()}
    assert not sy._posted_recently(old, NOW)
    assert sy._posted_recently(fresh, NOW)


def test_an_unreadable_stamp_does_not_block_a_post_for_ever():
    assert not sy._posted_recently({"last": "whenever"}, NOW)


def test_an_empty_register_entry_is_not_a_recent_post():
    assert not sy._posted_recently({}, NOW)


# ── the gates ──

def test_both_scans_have_to_pass(monkeypatch):
    calls = []

    def fake_scan(parts, **kw):
        calls.append(kw.get("mode"))
        ok = kw.get("mode") == "thread"       # the hook scan fails
        return {"ok": ok, "violations": [] if ok else ["g2h — not in any source"],
                "gate1": {}, "gate2": {}, "mode": kw.get("mode")}

    monkeypatch.setattr(sy.voice_gate, "scan", fake_scan)
    monkeypatch.setattr(sy.voice_gate, "format_report", lambda r: str(r["mode"]))
    ok, _ = sy.check_teaser(["hook with 42", "read it https://moltrust.ch/x"],
                            {"link": "https://moltrust.ch/x", "article_text": "42"})
    assert not ok, "a hook that fails rule (h) must not go out"
    assert calls == ["thread", "reply"]


def test_rule_h_is_given_the_blog_post_itself(monkeypatch):
    seen = {}

    def fake_scan(parts, **kw):
        if kw.get("mode") == "reply":
            seen.update(kw)
        return {"ok": True, "violations": [], "gate1": {}, "gate2": {},
                "mode": kw.get("mode")}

    monkeypatch.setattr(sy.voice_gate, "scan", fake_scan)
    monkeypatch.setattr(sy.voice_gate, "format_report", lambda r: "")
    art = "the article says 87% of them"
    sy.check_teaser(["hook", "link https://moltrust.ch/x"],
                    {"link": "https://moltrust.ch/x", "article_text": art})
    assert seen["sources"] == {"https://moltrust.ch/x": art}
    assert seen["source_text"] == art


# ── the register ──

def test_posting_records_the_url_and_counts_repeats(monkeypatch, tmp_path):
    monkeypatch.setattr(sy, "REGISTER_FILE", str(tmp_path / "syndicated.json"))
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "42 things")
    monkeypatch.setattr(sy, "draft_teaser", lambda i: ["hook 42", "link https://x"])
    monkeypatch.setattr(sy, "check_teaser", lambda p, i: (True, "ok"))
    monkeypatch.setattr(sy.x_post, "post_thread", lambda parts, kind=None: ["11", "12"])
    monkeypatch.setattr(sy, "bluesky_mirror", lambda parts: ["bsky://1"])
    monkeypatch.setattr(sy, "send_telegram", lambda *a, **k: True)

    reg = {}
    assert sy.post_evergreen(item("https://moltrust.ch/blog/a.html"), reg) == 0
    entry = json.load(open(tmp_path / "syndicated.json"))["https://moltrust.ch/blog/a.html"]
    assert entry["count"] == 1 and entry["tweet_ids"] == ["11", "12"]
    assert entry["bluesky"] == ["bsky://1"]


def test_a_blocked_teaser_is_not_recorded_as_posted(monkeypatch, tmp_path):
    monkeypatch.setattr(sy, "REGISTER_FILE", str(tmp_path / "syndicated.json"))
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "42")
    monkeypatch.setattr(sy, "draft_teaser", lambda i: ["hook", "link"])
    monkeypatch.setattr(sy, "check_teaser", lambda p, i: (False, "g2f — no number"))
    monkeypatch.setattr(sy, "send_telegram", lambda *a, **k: True)

    def boom(*a, **k):
        raise AssertionError("posted a blocked teaser")

    monkeypatch.setattr(sy.x_post, "post_thread", boom)
    reg = {}
    assert sy.post_evergreen(item("https://moltrust.ch/blog/a.html"), reg) == 1
    assert reg == {}


def test_an_empty_article_is_not_drafted_against(monkeypatch):
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "")

    def boom(*a, **k):
        raise AssertionError("drafted against nothing")

    monkeypatch.setattr(sy, "draft_teaser", boom)
    assert sy.post_evergreen(item("https://moltrust.ch/blog/a.html"), {}) == 1


def test_the_bluesky_link_is_one_a_person_can_open(monkeypatch):
    """The AT URI is what the API returns and belongs in the register; it is
    not something to paste into Telegram."""
    monkeypatch.setenv("BLUESKY_HANDLE", "moltrust.ch")
    web = sy.bluesky_web_url(
        "at://did:plc:bjrgfiemnynipuvv4h6md4qi/app.bsky.feed.post/3mwps3zsour2q")
    assert web == "https://bsky.app/profile/moltrust.ch/post/3mwps3zsour2q"


def test_something_that_is_not_an_at_uri_is_left_alone():
    assert sy.bluesky_web_url("—") == "—"
