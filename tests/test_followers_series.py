"""The hourly follower series: when a list read happens and what it records."""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "followers_series", ROOT / "scripts" / "followers_series.py")
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)

NOW = datetime.datetime(2026, 10, 13, 7, 5, tzinfo=datetime.timezone.utc)


def test_no_baseline_means_list():
    assert fs.needs_list([], 36)
    assert fs.needs_list([{"followers": 36, "listed": False}], 36), \
        "a failed baseline is retried, not forgotten"


def test_flat_count_reads_nothing():
    rows = [{"followers": 36, "listed": True, "baseline_ids": ["1"]},
            {"followers": 36, "listed": False}]
    assert not fs.needs_list(rows, 36)
    assert not fs.needs_list(rows, 35), "a drop is not an arrival"


def test_rise_reads_the_list():
    rows = [{"followers": 36, "listed": True, "baseline_ids": ["1"]}]
    assert fs.needs_list(rows, 37)


def test_held_hour_is_caught_up():
    """36 → 37 while reads were paused, then 37 again: the arrival is still
    looked for, against the last count whose list was read."""
    rows = [{"followers": 36, "listed": True, "baseline_ids": ["1"]},
            {"followers": 37, "listed": False, "list_skipped": "paused"}]
    assert fs.needs_list(rows, 37)
    assert fs.list_base(rows, 37) == 36


def test_new_arrivals_keep_order_and_skip_known():
    known = {"a", "b"}
    assert fs.new_arrivals(["n2", "a", "n1", "b"], known) == ["n2", "n1"]
    assert fs.new_arrivals(["a", "b"], known) == []


class _R:
    def __init__(self, body, status=200):
        self.status_code, self._b, self.text = status, body, json.dumps(body)

    def json(self):
        return self._b


def _wire(monkeypatch, tmp_path, count, follower_pages):
    path = tmp_path / "followers.jsonl"
    monkeypatch.setattr(fs, "series_path", lambda: str(path))
    monkeypatch.setattr(fs, "x_auth", lambda: object())
    monkeypatch.setattr(fs.x_meter, "reads_paused", lambda now=None: None)
    monkeypatch.setattr(fs.x_meter, "profiles_exhausted", lambda now=None: None)
    calls = []

    def get(url, params=None, auth=None, timeout=30, source=""):
        calls.append((url, dict(params or {}), source))
        if url.endswith("/users/me"):
            return _R({"data": {"id": "42", "public_metrics": {"followers_count": count}}})
        return _R(follower_pages.pop(0))

    monkeypatch.setattr(fs.x_meter, "get", get)
    return path, calls


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()]


def test_first_run_writes_a_baseline_of_ids_only(monkeypatch, tmp_path):
    path, calls = _wire(monkeypatch, tmp_path, 3, [
        {"data": [{"id": "1", "name": "N", "username": "u"}, {"id": "2"}],
         "meta": {"next_token": "t"}},
        {"data": [{"id": "3"}], "meta": {}}])
    assert fs.run(NOW) == 0
    row = _rows(path)[0]
    assert row["baseline_ids"] == ["1", "2", "3"] and row["listed"]
    assert "N" not in path.read_text() and "u\"" not in path.read_text(), \
        "ids only, no names or handles"
    assert all(src.startswith("followers-") for _, _, src in calls), "metered with a tag"


def test_rise_records_the_new_id_with_its_hour(monkeypatch, tmp_path):
    path, calls = _wire(monkeypatch, tmp_path, 4, [
        {"data": [{"id": "9"}, {"id": "3"}, {"id": "2"}], "meta": {}}])
    fs.append({"at": "x", "followers": 3, "listed": True,
               "baseline_ids": ["1", "2", "3"]}, str(path))
    assert fs.run(NOW) == 0
    row = _rows(path)[-1]
    assert row["new_ids"] == ["9"] and row["hour"] == "2026-10-13T07:00Z"
    assert calls[-1][1]["max_results"] == 1 + fs.LIST_MARGIN


def test_flat_hour_costs_one_profile_read(monkeypatch, tmp_path):
    path, calls = _wire(monkeypatch, tmp_path, 3, [])
    fs.append({"at": "x", "followers": 3, "listed": True,
               "baseline_ids": ["1", "2", "3"]}, str(path))
    assert fs.run(NOW) == 0
    assert len(calls) == 1 and _rows(path)[-1]["listed"] is False


def test_rise_without_unknown_id_is_flagged(monkeypatch, tmp_path):
    path, _ = _wire(monkeypatch, tmp_path, 4, [
        {"data": [{"id": "3"}, {"id": "2"}], "meta": {}}])
    fs.append({"at": "x", "followers": 3, "listed": True,
               "baseline_ids": ["1", "2", "3"]}, str(path))
    fs.run(NOW)
    row = _rows(path)[-1]
    assert row["new_ids"] == [] and row["unmatched"] == 1


def test_paused_reads_hold_the_list(monkeypatch, tmp_path):
    path, calls = _wire(monkeypatch, tmp_path, 4, [])
    monkeypatch.setattr(fs.x_meter, "reads_paused", lambda now=None: "paused")
    fs.append({"at": "x", "followers": 3, "listed": True,
               "baseline_ids": ["1", "2", "3"]}, str(path))
    fs.run(NOW)
    assert len(calls) == 1 and _rows(path)[-1]["list_skipped"] == "paused"
