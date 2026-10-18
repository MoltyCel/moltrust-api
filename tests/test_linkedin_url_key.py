"""LinkedIn series, ops CK2-CK4 (2026-10-18).

The post number in the URL is the key (CK4), a draft is answered by the post
that names it in `draft_at` or by a not-posted mark (CK3), and members reached
is the lead column (CK2). Each rule has a test that fires and one that stays
quiet.
"""
import importlib.util
import json
import pathlib
import sys

import pytest

EXPORT_URL = ("https://www.linkedin.com/posts/kerstenkroehl_since-4-march-2026-the-amazon-"
              "services-business-share-7515540568408711168-SnCv")
API_URL = "https://www.linkedin.com/feed/update/urn:li:share:7515540568408711168/"
OTHER_URL = ("https://www.linkedin.com/posts/kerstenkroehl_on-27-september-apollos-chief-"
             "economist-share-7516495215369814017-JZ66")


def _lm():
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "linkedin_metrics.py"
    spec = importlib.util.spec_from_file_location("li_metrics_ck", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lm(tmp_path):
    m = _lm()
    m.SERIES = str(tmp_path / "linkedin_metrics.jsonl")
    m.DRAFTS = str(tmp_path / "linkedin_drafts.jsonl")
    return m


def write(path, rows):
    pathlib.Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


# ── CK4: the post number is the key ──

def test_both_url_forms_give_the_same_post_number(lm):
    assert lm.post_id(EXPORT_URL) == lm.post_id(API_URL) == "7515540568408711168"
    assert lm.post_id("https://www.linkedin.com/posts/x-ugcPost-7511719123865800706-bnnK") \
        == "7511719123865800706"


def test_a_url_without_a_post_number_gives_none(lm):
    assert lm.post_id("https://www.linkedin.com/feed/update/x/") is None
    assert lm.post_id(None) is None


def test_one_post_under_two_urls_and_two_keys_is_one_line(lm):
    rows = [{"key": None, "url": API_URL, "posted_at": "2026-10-12T22:35:04.630465+00:00",
             "read_at": "2026-10-12T22:35:05+00:00"},
            {"key": "7cf792447f846709", "url": API_URL, "posted_at": "2026-10-12T22:35:04+00:00",
             "impressions": 206, "members_reached": 143, "read_at": "2026-10-15T10:00:00+00:00"},
            {"url": EXPORT_URL, "posted_at": "2026-10-12T22:35:00+00:00",
             "impressions": 215, "members_reached": 148, "read_at": "2026-10-18T11:00:00+00:00"}]
    out = lm.latest_per_post(rows)
    assert len(out) == 1
    assert (out[0]["impressions"], out[0]["members_reached"]) == (215, 148)


def test_two_posts_stay_two_lines(lm):
    rows = [{"url": EXPORT_URL, "impressions": 215, "read_at": "1"},
            {"url": OTHER_URL, "impressions": 217, "read_at": "2"}]
    assert len(lm.latest_per_post(rows)) == 2


# ── CK3: a draft is answered by its post or by a not-posted mark ──

def test_a_posted_draft_is_answered_through_draft_at(lm):
    write(lm.DRAFTS, [{"at": "2026-10-15T14:31:27.348331+00:00",
                       "title": "A Bank Run Without the Queue"}])
    write(lm.SERIES, [{"url": OTHER_URL, "posted_at": "2026-10-15T13:48:00+00:00",
                       "impressions": 217, "draft_at": "2026-10-15T14:31:27.348331+00:00"}])
    assert lm.outstanding() == []


def test_a_post_without_draft_at_leaves_the_draft_owed(lm):
    write(lm.DRAFTS, [{"at": "2026-10-15T14:31:27.348331+00:00",
                       "title": "A Bank Run Without the Queue"}])
    write(lm.SERIES, [{"url": OTHER_URL, "posted_at": "2026-10-15T13:48:00+00:00",
                       "impressions": 217}])
    assert [d["title"] for d in lm.outstanding()] == ["A Bank Run Without the Queue"]


def test_a_draft_marked_not_posted_is_not_owed_and_shows_as_such(lm, tmp_path):
    write(lm.DRAFTS, [{"at": "2026-10-08T05:01:23.243276+00:00",
                       "title": "Who proves what the agent was allowed to buy"},
                      {"at": "2026-10-12T21:30:49.812504+00:00",
                       "title": "Budget limits for AI agents"}])
    f = tmp_path / "in.json"
    f.write_text(json.dumps([
        {"not_posted": True, "key": "not-posted-2026-10-08T05:01",
         "draft_at": "2026-10-08T05:01:23.243276+00:00",
         "topic": "Who proves what the agent was allowed to buy"},
        {"not_posted": True, "key": "not-posted-2026-10-12T21:30",
         "draft_at": "2026-10-12T21:30:49.812504+00:00",
         "topic": "Budget limits for AI agents"}]))
    assert lm.record(str(f)) == 0
    assert lm.outstanding() == []
    t = lm.table()
    assert "2026-10-08  — nicht gepostet  Who proves what the agent" in t
    assert "2026-10-12  — nicht gepostet  Budget limits for AI agents" in t


# ── CK2: reached leads the table, impressions follow ──

def test_reach_is_the_first_figure_column(lm):
    write(lm.SERIES, [{"url": EXPORT_URL, "posted_at": "2026-10-12T22:35:00+00:00",
                       "format": "text", "impressions": 215, "members_reached": 148}])
    head, line = lm.table().splitlines()[:2]
    assert head.split()[2:4] == ["Reach", "Impr"]
    assert line.split()[2:4] == ["148", "215"]


def test_the_hook_verdict_follows_reach_not_impressions(lm):
    rows = ([{"url": f"https://x/share-{7500000000000000000 + i}-a", "hook_type": "actor",
              "members_reached": 200 + i, "impressions": 210} for i in range(3)]
            + [{"url": f"https://x/share-{7600000000000000000 + i}-a", "hook_type": "claim",
                "members_reached": 50 + i, "impressions": 900} for i in range(3)])
    assert "Urteil: Akteur erreicht mehr" in lm.compare_hooks(rows)
