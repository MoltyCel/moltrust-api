"""Reply measurement for @MolTrust is off (ops CJ10, 22.10.2026).

A reply still sits in the radar state as posted and inside the two-week window,
so the measurement would pick it up if the switch did not hold. The daily run
writes the digest row and nothing of kind reply, and the reply ids are not
even asked for, because every id in the call is billed.
"""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "digest_metrics", ROOT / "scripts" / "digest_metrics.py")
dm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dm)


def _open_reply_in_state(tmp_path, monkeypatch):
    posted = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
    state = {"decisions": {"900": {"result": "posted", "reply_id": "901",
                                   "posted_at": posted.isoformat(),
                                   "route": "api"}}}
    f = tmp_path / "reply_radar_state.json"
    f.write_text(json.dumps(state))
    monkeypatch.setattr(dm, "RADAR_STATE", str(f))
    # The precondition: without the switch, this reply would be measured.
    assert [t["reply_id"] for t in dm.tracked_replies(
        datetime.datetime.now(datetime.timezone.utc))] == ["901"]


def _offline(monkeypatch, asked, written):
    monkeypatch.setattr(dm, "x_auth", lambda: object())
    monkeypatch.setattr(dm, "last_digest", lambda: ("800", "2026-10-22"))
    monkeypatch.setattr(dm, "tracked_videos", lambda now: [])
    monkeypatch.setattr(dm, "followers", lambda auth: 30)

    def fetch_many(ids, auth):
        asked.extend(ids)
        return {i: {"id": i, "public_metrics": {"impression_count": 5}}
                for i in ids}
    monkeypatch.setattr(dm, "fetch_many", fetch_many)
    monkeypatch.setattr(dm, "append", written.append)
    monkeypatch.setattr(dm, "notify", type("N", (), {
        "STATS": "stats", "send_telegram": staticmethod(lambda *a, **k: None)}))


def test_the_daily_run_writes_digest_rows_only(tmp_path, monkeypatch):
    _open_reply_in_state(tmp_path, monkeypatch)
    asked, written = [], []
    _offline(monkeypatch, asked, written)

    assert dm.run_all(quiet=True) == 0
    assert written, "the digest row is still written"
    assert {r.get("kind", "digest") for r in written} == {"digest"}
    assert asked == ["800"]


def test_replies_only_writes_nothing(tmp_path, monkeypatch):
    _open_reply_in_state(tmp_path, monkeypatch)
    asked, written = [], []
    _offline(monkeypatch, asked, written)

    assert dm.measure_replies(quiet=True) == 0
    assert written == [] and asked == []
