"""The video series and its comparison against the digest series."""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "digest_metrics", ROOT / "scripts" / "digest_metrics.py")
dm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dm)

NOW = datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.timezone.utc)


def clip(at=NOW, post="111", uri="at://x/app.bsky.feed.post/abc"):
    return {"at": at.isoformat(), "clip": "lobster-clip1.mp4",
            "x": {"post": post, "url": f"https://x.com/MolTrust/status/{post}"},
            "bluesky": {"uri": uri, "url": "https://bsky.app/x"},
            "linkedin": {"channel": "linkedin", "posted_by": "hand", "url": None}}


def test_the_two_networks_stay_apart(monkeypatch, tmp_path):
    """X reports impressions and Bluesky reports none; one combined figure
    would be X's number wearing both names."""
    written = []
    monkeypatch.setattr(dm, "append", written.append)
    monkeypatch.setattr(dm, "bsky_followers", lambda handle="moltrust.ch": 7)
    monkeypatch.setattr(dm, "bsky_counts", lambda uri: {
        "likes": 3, "reposts": 1, "replies": 2, "quotes": 0, "impressions": None})
    monkeypatch.setattr(dm, "notify", type("N", (), {
        "send_telegram": staticmethod(lambda *a, **k: True), "STATS": "s"})())

    metrics = {"111": {"public_metrics": {"impression_count": 240, "like_count": 5,
                                          "retweet_count": 2, "reply_count": 1,
                                          "quote_count": 0}}}
    assert dm.write_video_rows([clip()], metrics, 32, NOW, quiet=True) == 0
    row = written[0]
    assert row["kind"] == "video"
    assert row["x"]["impressions"] == 240 and row["x"]["followers_now"] == 32
    assert row["bluesky"]["impressions"] is None, "zero would read as none"
    assert row["bluesky"]["likes"] == 3 and row["bluesky"]["followers_now"] == 7
    assert row["linkedin"]["posted_by"] == "hand"


def test_the_linkedin_channel_is_carried_without_a_url(monkeypatch):
    written = []
    monkeypatch.setattr(dm, "append", written.append)
    monkeypatch.setattr(dm, "bsky_followers", lambda handle="moltrust.ch": 1)
    monkeypatch.setattr(dm, "bsky_counts", lambda uri: {})
    dm.write_video_rows([clip()], {}, 1, NOW, quiet=True)
    assert written[0]["linkedin"]["url"] is None


def test_a_clip_outside_the_window_is_dropped(monkeypatch, tmp_path):
    old = clip(at=NOW - datetime.timedelta(days=dm.VIDEO_TRACK_DAYS + 1))
    fresh = clip(at=NOW - datetime.timedelta(days=1), post="222")
    led = tmp_path / "video_posts.jsonl"
    led.write_text(json.dumps(old) + "\n" + json.dumps(fresh) + "\n")
    monkeypatch.setattr(dm, "VIDEO_LEDGER", str(led))
    rows = dm.tracked_videos(NOW)
    assert [r["x"]["post"] for r in rows] == ["222"]


# ── the comparison ──

def metrics_file(tmp_path, rows):
    f = tmp_path / "digest_metrics.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(f)


def measured(days_ago, **kw):
    base = {"measured_at": (NOW - datetime.timedelta(days=days_ago)).isoformat()}
    base.update(kw)
    return base


def test_a_post_measured_daily_counts_once(monkeypatch, tmp_path, capsys):
    """Fourteen days of rows for one post would otherwise read as 14 posts."""
    rows = [measured(d, kind="video", clip="c1",
                     x={"impressions": 100 + d, "followers_now": 32})
            for d in range(3)]
    rows += [measured(d, kind="digest", tweet_id=f"d{d}", impressions=50)
             for d in range(3)]
    monkeypatch.setattr(dm, "METRICS_FILE", metrics_file(tmp_path, rows))
    dm.compare_video_digest(days=7, quiet=True)
    out = capsys.readouterr().out
    assert "Video: 1 Post(s)" in out, out
    assert "Digest: 3 Post(s)" in out, out


def test_the_ratio_is_per_post(monkeypatch, tmp_path, capsys):
    rows = [measured(0, kind="video", clip="c1",
                     x={"impressions": 200, "followers_now": 32}),
            measured(0, kind="digest", tweet_id="d1", impressions=100),
            measured(0, kind="digest", tweet_id="d2", impressions=100)]
    monkeypatch.setattr(dm, "METRICS_FILE", metrics_file(tmp_path, rows))
    dm.compare_video_digest(days=7, quiet=True)
    out = capsys.readouterr().out
    assert "2.00×" in out, out


def test_bluesky_is_named_as_absent_from_the_comparison(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dm, "METRICS_FILE", metrics_file(
        tmp_path, [measured(0, kind="video", clip="c1",
                            x={"impressions": 1, "followers_now": 1})]))
    dm.compare_video_digest(days=7, quiet=True)
    out = capsys.readouterr().out
    assert "keine Impressionen" in out
    assert "Digest: keine Posts im Fenster" in out


def test_rows_older_than_the_window_are_ignored(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dm, "METRICS_FILE", metrics_file(
        tmp_path, [measured(30, kind="video", clip="c1",
                            x={"impressions": 9999, "followers_now": 1})]))
    dm.compare_video_digest(days=7, quiet=True)
    assert "Video: keine Posts" in capsys.readouterr().out
