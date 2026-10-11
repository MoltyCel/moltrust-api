import datetime as dt
import json
import subprocess

from scripts import checkout_dirty as cd

NOW = dt.datetime(2026, 10, 11, 9, 0, tzinfo=dt.timezone.utc)


def _repo(tmp_path, name="co"):
    r = tmp_path / name
    r.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)
    g("init", "-q")
    (r / "a.py").write_text("x = 1\n")
    g("add", "a.py")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return r


def _run(tmp_path, repo, now=NOW, baseline=None):
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps({"untracked": baseline or {}}))
    return cd.report(now, {"moltrust-api": str(repo)}, str(tmp_path / "seen.json"), str(bl))


def test_clean_checkout_says_so(tmp_path):
    assert _run(tmp_path, _repo(tmp_path)) == ["CHECKOUT SAUBER"]


def test_modified_file_names_path_lines_and_first_seen(tmp_path):
    r = _repo(tmp_path)
    (r / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
    out = _run(tmp_path, r)
    assert len(out) == 1
    assert "moltrust-api M a.py +2/-0" in out[0]
    assert "zuerst gesehen 2026-10-11T09:00Z" in out[0]


def test_first_seen_is_kept_across_runs_and_dropped_when_clean(tmp_path):
    r = _repo(tmp_path)
    (r / "a.py").write_text("x = 2\n")
    _run(tmp_path, r)
    later = _run(tmp_path, r, NOW + dt.timedelta(hours=3))
    assert "zuerst gesehen 2026-10-11T09:00Z" in later[0]
    (r / "a.py").write_text("x = 1\n")
    assert _run(tmp_path, r, NOW + dt.timedelta(hours=4)) == ["CHECKOUT SAUBER"]
    (r / "a.py").write_text("x = 3\n")
    again = _run(tmp_path, r, NOW + dt.timedelta(hours=5))
    assert "zuerst gesehen 2026-10-11T14:00Z" in again[0]


def test_new_untracked_file_counts_baseline_does_not(tmp_path):
    r = _repo(tmp_path)
    (r / "old.py").write_text("a\n")
    (r / "new.py").write_text("a\nb\n")
    out = _run(tmp_path, r, baseline={"moltrust-api": ["old.py"]})
    assert len(out) == 1 and "?? new.py +2" in out[0]


def test_missing_checkout_is_unreadable_not_clean(tmp_path):
    out = cd.report(NOW, {"moltrust-api": str(tmp_path / "nope")},
                    str(tmp_path / "s.json"), str(tmp_path / "bl.json"))
    assert out[0].startswith("UNREADABLE")
    assert "CHECKOUT SAUBER" not in out
