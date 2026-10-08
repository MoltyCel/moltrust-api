"""Evergreen syndication: which archive post goes out, and what it may say."""
import datetime
import json

import pytest

from agents import syndicate as sy


NOW = datetime.datetime(2026, 9, 30, 9, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def _armed(monkeypatch, tmp_path):
    """The posting tests below describe the armed path. Unarmed has its own tests."""
    monkeypatch.setattr(sy, "SECRETS_FILE", str(tmp_path / "no-secrets"))
    monkeypatch.setenv("SYNDICATE_ARMED", "1")
    # The guards in front of every post (2026-10-08): a state that exists, a
    # counter of its own, an open breaker, a fresh run.
    state = tmp_path / "syndicate_state.json"
    state.write_text(json.dumps({"seen": []}))
    monkeypatch.setattr(sy, "STATE_FILE", str(state))
    monkeypatch.setattr(sy, "COUNTER_FILE", str(tmp_path / "syndicate_counter.json"))
    monkeypatch.setattr(sy.x_meter, "reads_paused", lambda now=None: None)
    monkeypatch.setattr(sy, "_RUN_POSTS", 0)


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
    monkeypatch.setattr(sy, "draft_teaser",
                        lambda i: {"parts": ["hook 42", "link https://x"],
                                   "linkedin": "a hundred and forty words"})
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
    monkeypatch.setattr(sy, "draft_teaser",
                        lambda i: {"parts": ["hook", "link"], "linkedin": ""})
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


# ── the LinkedIn handoff ──

def _wire_a_successful_post(monkeypatch, tmp_path, linkedin):
    sent = []
    monkeypatch.setattr(sy, "REGISTER_FILE", str(tmp_path / "syndicated.json"))
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "42 things")
    monkeypatch.setattr(sy, "draft_teaser",
                        lambda i: {"parts": ["hook 42", "link https://x"],
                                   "linkedin": linkedin})
    monkeypatch.setattr(sy, "check_teaser", lambda p, i: (True, "ok"))
    monkeypatch.setattr(sy.x_post, "post_thread", lambda parts, kind=None: ["11", "12"])
    monkeypatch.setattr(sy, "bluesky_mirror", lambda parts:
                        ["at://did:plc:x/app.bsky.feed.post/abc"])
    monkeypatch.setattr(sy, "send_telegram",
                        lambda text, **k: sent.append(text) or True)
    return sent


def test_every_run_hands_over_a_linkedin_draft(monkeypatch, tmp_path):
    """LinkedIn has no write path here, so the draft goes where it can be
    pasted — the same handoff the regular syndication path makes."""
    sent = _wire_a_successful_post(monkeypatch, tmp_path,
                                   "Agents were set a task and took the "
                                   "shortest route. " * 8)
    assert sy.post_evergreen(item("https://moltrust.ch/blog/a.html"), {}) == 0
    drafts = [t for t in sent if "LinkedIn draft" in t]
    assert len(drafts) == 1
    assert "paste into the MolTrust page" in drafts[0]
    assert "x.com/MolTrust/status/11" in drafts[0]
    assert "bsky.app/profile/" in drafts[0], "the AT URI leaked into the message"


def test_the_register_records_that_a_draft_was_handed_over(monkeypatch, tmp_path):
    _wire_a_successful_post(monkeypatch, tmp_path, "some words")
    reg = {}
    sy.post_evergreen(item("https://moltrust.ch/blog/a.html"), reg)
    assert reg["https://moltrust.ch/blog/a.html"]["linkedin_drafted"] is True


def test_a_missing_linkedin_draft_does_not_fail_the_post(monkeypatch, tmp_path):
    """The X thread is already up; a missing draft is a note, not a failure."""
    sent = _wire_a_successful_post(monkeypatch, tmp_path, "")
    assert sy.post_evergreen(item("https://moltrust.ch/blog/a.html"), {}) == 0
    assert not any("LinkedIn draft" in t for t in sent)


def test_a_draft_that_ran_out_of_tokens_is_not_used(monkeypatch):
    """At max_tokens 1500 every output token went to thinking and the text
    block never arrived — stop_reason max_tokens, content [thinking, ""]."""
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"stop_reason": "max_tokens",
                    "content": [{"type": "thinking", "text": ""}]}

    monkeypatch.setattr(sy, "load_anthropic_key", lambda: "k")
    monkeypatch.setattr(sy.httpx, "post", lambda *a, **k: R())
    assert sy.draft_teaser(item("https://moltrust.ch/blog/a.html")) is None


def test_a_thinking_block_is_not_mistaken_for_the_answer(monkeypatch):
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"stop_reason": "end_turn", "content": [
                {"type": "thinking", "text": "{\"hook\": \"not this one\"}"},
                {"type": "text", "text": '{"hook": "h 42", "link_post": "l",'
                                         ' "linkedin": "li"}'}]}

    monkeypatch.setattr(sy, "load_anthropic_key", lambda: "k")
    monkeypatch.setattr(sy.httpx, "post", lambda *a, **k: R())
    out = sy.draft_teaser(item("https://moltrust.ch/blog/a.html"))
    assert out["parts"][0] == "h 42"


# ── unarmed: draft to Telegram, nothing posted (2026-10-08) ──

def _no_external(monkeypatch, calls):
    def boom(*a, **k):
        calls.append("external")
        raise AssertionError("posted while unarmed")
    monkeypatch.setattr(sy.x_post, "post_thread", boom)
    monkeypatch.setattr(sy, "bluesky_mirror", boom)


def _evergreen_setup(monkeypatch, tmp_path, sent):
    monkeypatch.setattr(sy, "REGISTER_FILE", str(tmp_path / "syndicated.json"))
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "42 things")
    monkeypatch.setattr(sy, "draft_teaser",
                        lambda i: {"parts": ["hook 42", "read https://moltrust.ch/x"],
                                   "linkedin": "li text"})
    monkeypatch.setattr(sy, "check_teaser", lambda p, i: (True, "ok"))
    monkeypatch.setattr(sy, "send_telegram", lambda m, **k: sent.append(m) or True)


def test_unarmed_evergreen_sends_a_draft_and_posts_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("SYNDICATE_ARMED", raising=False)
    calls, sent = [], []
    _no_external(monkeypatch, calls)
    _evergreen_setup(monkeypatch, tmp_path, sent)
    code = sy.post_evergreen(item("https://moltrust.ch/x"), {})
    assert code == 0
    assert calls == []
    assert len(sent) == 1 and "nicht gepostet" in sent[0] and "hook 42" in sent[0]
    assert not (tmp_path / "syndicated.json").exists(), "a held draft is not a post"


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_the_literal_one_arms(monkeypatch, value):
    monkeypatch.setenv("SYNDICATE_ARMED", value)
    assert sy.armed()[0] is False


def test_the_variable_in_the_secrets_file_does_not_arm(monkeypatch, tmp_path):
    f = tmp_path / "secrets"
    f.write_text("FOO=1\nSYNDICATE_ARMED=1\n")
    monkeypatch.setattr(sy, "SECRETS_FILE", str(f))
    monkeypatch.setenv("SYNDICATE_ARMED", "1")
    ok, why = sy.armed()
    assert ok is False and "Cron-Zeile" in why


def test_unarmed_new_post_is_held_and_marked_done(monkeypatch):
    monkeypatch.delenv("SYNDICATE_ARMED", raising=False)
    calls, sent = [], []
    _no_external(monkeypatch, calls)
    monkeypatch.setattr(sy, "fetch_article_text", lambda u, limit=6000: "42")
    monkeypatch.setattr(sy, "draft", lambda i: {"thread": ["one 42", "two https://moltrust.ch/x"],
                                                "linkedin": "li"})
    monkeypatch.setattr(sy.voice_gate, "scan", lambda parts, **kw:
                        {"ok": True, "violations": [], "gate1": {}, "gate2": {}, "mode": "thread"})
    monkeypatch.setattr(sy.voice_gate, "format_report", lambda r: "")
    monkeypatch.setattr(sy, "send_telegram", lambda m, **k: sent.append(m) or True)
    state = {}
    done = sy.process_item(item("https://moltrust.ch/x"), state)
    assert done is True
    assert calls == []
    rec = state["items"]["https://moltrust.ch/x"]
    assert rec["status"] == "held_unarmed" and "nicht gesetzt" in rec["held_reason"]
    assert len(sent) == 1 and "one 42" in sent[0]


def test_the_linkedin_draft_is_scanned_against_the_article(monkeypatch):
    """2026-10-08: the draft was blocked by g2g because no source was passed."""
    seen = {}

    def fake_scan(parts, **kw):
        seen.update(kw)
        return {"ok": True, "violations": [], "gate1": {}, "gate2": {}, "mode": kw.get("mode")}

    monkeypatch.setattr(sy.voice_gate, "scan", fake_scan)
    monkeypatch.setattr(sy.voice_gate, "format_report", lambda r: "")
    monkeypatch.setattr(sy, "send_telegram", lambda *a, **k: True)
    monkeypatch.setattr(sy.notify, "send_telegram_message", lambda *a, **k: True)
    import agents.linkedin_post as lp
    monkeypatch.setattr(lp, "remember", lambda *a, **k: None)
    sy.deliver_linkedin({"link": "https://moltrust.ch/x", "title": "T",
                         "article_text": "the article with 31.43"}, "draft 31.43")
    assert seen.get("source_text") == "the article with 31.43"


def test_a_missing_category_is_logged_not_silent(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="syndicate"):
        cat, tier = sy.register_of({"link": "https://moltrust.ch/x", "category": ""})
    assert cat == "Analysis" and tier == sy.DEFAULT_TIER
    assert any("no <category>" in r.message for r in caplog.records)


def test_a_known_category_is_used_without_a_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="syndicate"):
        cat, tier = sy.register_of({"link": "https://moltrust.ch/x", "category": "Opinion"})
    assert tier == sy.REGISTER_TIER["opinion"]
    assert not caplog.records
