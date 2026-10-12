"""The 18.10 decision basis: new context sections, the verdict left alone,
and the syndication and digest rows that feed the comparison."""
import datetime
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ri = _load("reply_impact", "scripts/reply_impact.py")
dm = _load("digest_metrics_basis", "scripts/digest_metrics.py")

UTC = datetime.timezone.utc


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def world(tmp_path, monkeypatch):
    metrics = tmp_path / "digest_metrics.jsonl"
    rows = [
        # followers at 18:00 on 04.10 and 05.10, and a 10:18 reading that loses
        {"kind": "reply", "measured_at": "2026-10-04T10:18:00+00:00",
         "tweet_id": "r0", "posted_at": "2026-10-02T07:00:00.000Z",
         "impressions": 9, "profile_clicks": 0, "followers_now": 34},
        {"kind": "digest", "measured_at": "2026-10-04T18:00:00+00:00",
         "tweet_id": "d1", "posted_at": "2026-10-04T12:00:10.000Z",
         "age_hours": 6.0, "impressions": 8, "likes": 0, "followers_now": 35},
        {"kind": "reply", "measured_at": "2026-10-05T18:00:00+00:00",
         "tweet_id": "r1", "posted_at": "2026-10-05T06:28:43.000Z",
         "age_hours": 11.5, "impressions": 47, "likes": 1, "profile_clicks": 1,
         "engagements": 3, "followers_now": 37},
        {"kind": "digest", "measured_at": "2026-10-05T18:00:00+00:00",
         "tweet_id": "d2", "posted_at": "2026-10-05T12:00:13.000Z",
         "age_hours": 6.0, "impressions": 10, "likes": 1, "profile_clicks": 2,
         "followers_now": 37},
        {"kind": "syndication", "measured_at": "2026-10-05T18:00:00+00:00",
         "tweet_id": "s1", "posted_at": "2026-10-05T05:01:17.000Z",
         "age_hours": 13.0, "impressions": 30, "likes": 2, "profile_clicks": 1,
         "followers_now": 37},
    ]
    _jsonl(metrics, rows)
    posts = tmp_path / "x_posts.jsonl"
    _jsonl(posts, [
        {"at": "2026-10-04T12:00:10+00:00", "kind": "digest", "id": "d1"},
        {"at": "2026-10-04T12:00:11+00:00", "kind": "digest-part", "id": "d1p"},
        {"at": "2026-10-05T05:01:17+00:00", "kind": "syndication", "id": "s1"},
        {"at": "2026-10-05T05:01:18+00:00", "kind": "syndication-part", "id": "s1p"},
        {"at": "2026-10-05T12:00:13+00:00", "kind": "digest", "id": "d2"},
    ])
    state = tmp_path / "reply_radar_state.json"
    state.write_text(json.dumps({"decisions": {"t1": {
        "result": "posted", "reply_id": "r1",
        "posted_at": "2026-10-05T06:28:43.000Z"}}, "by_prompt": {}}))
    ledger = tmp_path / "x_meter.jsonl"
    _jsonl(ledger, [
        {"at": "2026-10-05T06:05:00+00:00", "kind": "read", "source": "tweets",
         "posts": ["a", "b"], "users": ["u1"]},
        {"at": "2026-10-05T10:05:00+00:00", "kind": "read", "source": "tweets",
         "posts": ["a"], "users": []},            # same day: billed once
        {"at": "2026-10-05T10:06:00+00:00", "kind": "read", "source": "2105855276536725599",
         "posts": ["c"], "users": []},
        {"at": "2026-10-05T18:00:00+00:00", "kind": "read", "source": "tweets-batch",
         "posts": ["d1", "d2"], "users": []},     # digest_metrics, not the radar
        {"at": "2026-10-05T12:00:00+00:00", "kind": "write", "source": "digest",
         "id": "d2", "with_url": False},
    ])
    log = tmp_path / "reply_radar.log"
    log.write_text(
        "[2026-10-05T06:05:00] INFO:   tokens in=100 out=100 cache_read=0 cache_write=0 stop=end_turn\n"
        "[2026-10-11T16:05:08] INFO:   tokens in=148 out=43 cache_read=0 cache_write=32196 stop=end_turn\n"
        "[2026-10-11T16:05:11] INFO:   tokens in=174 out=100 cache_read=32196 cache_write=0 stop=end_turn\n"
        "[2026-10-11T16:05:12] INFO: Done: 1 drafts this run\n")
    for attr, p in (("METRICS", metrics), ("POSTS", posts), ("RADAR_STATE", state),
                    ("X_LEDGER", ledger), ("RADAR_LOG", log),
                    ("FOLLOWERS", tmp_path / "followers.jsonl")):
        monkeypatch.setattr(ri, attr, str(p))
    return tmp_path


def test_radar_model_is_the_priced_one():
    src = (ROOT / "agents" / "reply_radar.py").read_text()
    assert re.search(r'^MODEL = "%s"' % re.escape(ri.RADAR_MODEL), src, re.M), \
        "the radar changed model; MODEL_USD_PER_MTOK prices the old one"


def test_cost_aggregation(world):
    assert ri.radar_x_usd("2026-10-05") == pytest.approx(3 * 0.005 + 0.010)
    m = ri.model_usd_by_day()
    expected = ((148 + 174) * 5 + (43 + 100) * 25 + 32196 * 0.5 + 32196 * 6.25) / 1e6
    assert m["2026-10-11"] == pytest.approx(expected, abs=0.001)
    c = ri.costs("2026-10-04", "2026-10-11")
    by = {r["day"]: r for r in c["days"]}
    assert by["2026-10-05"]["model_radar"] is None, \
        "before the token log the model cost is unknown, not zero"
    assert by["2026-10-05"]["x_total"] >= by["2026-10-05"]["x_radar"]
    assert c["model_floor"] is True


def test_followers_on_start_and_decision(world):
    now = datetime.datetime(2026, 10, 18, 7, 30, tzinfo=UTC)
    start = ri.followers_on("2026-10-04", now)
    assert start["followers"] == 35 and not start["provisional"], \
        "the 18:00 reading wins over the 10:18 one"
    dec = ri.followers_on("2026-10-18", now)
    assert dec["provisional"] and dec["followers"] == 37
    assert dec["at"].startswith("2026-10-05T18:00")


def test_decision_day_after_18_uses_the_hourly_row(world):
    _jsonl(world / "followers.jsonl", [
        {"at": "2026-10-18T17:00:02+00:00", "followers": 40},
        {"at": "2026-10-18T18:00:02+00:00", "followers": 41}])
    now = datetime.datetime(2026, 10, 18, 18, 10, tzinfo=UTC)
    dec = ri.followers_on("2026-10-18", now)
    assert dec == {"day": "2026-10-18", "followers": 41,
                   "at": "2026-10-18T18:00:02+00:00", "provisional": False}


def test_follower_steps_list_preceding_posts(world):
    now = datetime.datetime(2026, 10, 6, tzinfo=UTC)
    steps = ri.follower_steps("2026-10-04", now)
    assert len(steps) == 1
    s = steps[0]
    assert (s["from"], s["to"], s["resolution"]) == (35, 37, "Tag")
    kinds = sorted(p["kind"] for p in s["posts"])
    assert kinds == ["digest", "reply", "syndication"], \
        "thread parts are not posts; the 04.10 digest is outside the 24 h"


def test_hourly_steps_carry_new_ids(world):
    _jsonl(world / "followers.jsonl", [
        {"at": "2026-10-05T19:00:00+00:00", "followers": 37},
        {"at": "2026-10-05T20:00:00+00:00", "followers": 38, "new_ids": ["9"]}])
    steps = ri.follower_steps("2026-10-04", datetime.datetime(2026, 10, 6, tzinfo=UTC))
    assert [s["resolution"] for s in steps] == ["Tag", "Stunde"]
    assert steps[-1]["new_ids"] == 1


def test_side_by_side_counts_clicks_only_where_known(world):
    sbs = ri.side_by_side("2026-10-04")
    assert sbs["digest"]["posts"] == 2 and sbs["digest"]["clicks_known"] == 1
    assert sbs["digest"]["clicks_per_post"] == 2.0, "a missing column is not a zero"
    assert sbs["syndication"]["impressions_median"] == 30
    assert sbs["reply"]["clicks_per_post"] == 1.0


def test_report_has_the_new_sections_and_the_same_verdict(world):
    now = datetime.datetime(2026, 10, 18, 7, 30, tzinfo=UTC)
    k = ri.collect("2026-10-04", now=now)
    text = ri.format_report(k)
    for head in ("Drei Reihen im selben Fenster", "Kosten im Fenster",
                 "Follower</b>", "Follower nach Posts", "keine Zuordnung",
                 "vorläufig", "Untergrenze"):
        assert head in text, head
    # The verdict reads only the reply window, exactly as before.
    base = ri._collect("2026-10-04")
    assert ri.verdict(k) == ri.verdict(base)
    v = ri.verdict(k)
    assert v["replies"] == 1 and v["median_impressions"] == 47
    assert v["clicks_ok"] and v["median_ok"] and v["continue"]


def test_thresholds_unchanged():
    assert ri.MEDIAN_IMPRESSIONS_MIN == 30 and ri.CLICKS_PER_REPLY_MIN == 0.5


# ── the rows that feed it ──

NOW = datetime.datetime(2026, 10, 12, 18, tzinfo=UTC)


def test_syndication_row_has_the_reply_columns():
    data = {"created_at": "2026-10-08T05:01:17.000Z",
            "public_metrics": {"impression_count": 120, "like_count": 3,
                               "reply_count": 1, "retweet_count": 0},
            "non_public_metrics": {"user_profile_clicks": 2, "engagements": 9}}
    row = dm.syndication_row({"tweet_id": "s1", "path": "new"}, data, 36, NOW)
    reply_cols = {"impressions", "profile_clicks", "engagements", "likes",
                  "replies", "retweets"}
    assert reply_cols <= set(row)
    assert row["kind"] == "syndication" and row["profile_clicks"] == 2
    assert row["age_hours"] == pytest.approx(108.98, abs=0.1)


def test_syndication_row_keeps_missing_clicks_as_none():
    row = dm.syndication_row({"tweet_id": "s1"},
                             {"public_metrics": {"impression_count": 5}}, None, NOW)
    assert row["profile_clicks"] is None


def test_digest_row_carries_profile_clicks(monkeypatch):
    written = []
    monkeypatch.setattr(dm, "append", written.append)
    data = {"created_at": "2026-10-12T12:00:14.000Z",
            "public_metrics": {"impression_count": 22},
            "non_public_metrics": {"user_profile_clicks": 1, "engagements": 4}}
    assert dm.write_digest_row("d", "2026-10-12", data, NOW, quiet=True,
                               followers_now=36) == 0
    assert written[0]["profile_clicks"] == 1 and written[0]["engagements"] == 4


def test_tracked_syndication_dedupes_three_sources(monkeypatch, tmp_path):
    (tmp_path / "x_posts.jsonl").write_text(json.dumps(
        {"at": "2026-10-08T05:01:17+00:00", "kind": "syndication", "id": "h2"}) + "\n"
        + json.dumps({"at": "2026-10-08T05:01:18+00:00", "kind": "syndication-part",
                      "id": "p"}) + "\n")
    (tmp_path / "syndicate_state.json").write_text(json.dumps({"items": {
        "g1": {"tweet_ids": ["h1", "x"], "posted_at": "2026-10-01T08:59:00+00:00"},
        "g2": {"tweet_ids": ["h2", "y"], "posted_at": "2026-10-08T05:01:20+00:00"},
        "g3": {"status": "blocked"}}}))
    (tmp_path / "syndicated.json").write_text(json.dumps({
        "https://a": {"tweet_ids": ["h1"], "last": "2026-10-01T08:55:36+00:00"},
        "https://old": {"tweet_ids": ["h0"], "last": "2026-09-01T07:00:00+00:00"}}))
    monkeypatch.setattr(dm, "_data", lambda *p: str(tmp_path.joinpath(*p)))
    hooks = dm.tracked_syndication(NOW)
    assert [h["tweet_id"] for h in hooks] == ["h1", "h2"], \
        "parts, duplicates and posts past 14 days are left out"
