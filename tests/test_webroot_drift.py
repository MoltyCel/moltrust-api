"""Web-Root gegen Repo (scripts/webroot_drift.py, 2026-10-08)."""
import hashlib
import json
import subprocess

from scripts import webroot_drift as w


def _setup(tmp_path, committed: dict, served: dict, baseline: dict):
    repo = tmp_path / "repo"; repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                    capture_output=True, text=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    for p, b in committed.items():
        (repo / p).parent.mkdir(parents=True, exist_ok=True); (repo / p).write_bytes(b)
    git("add", "-A"); git("commit", "-q", "-m", "c")
    sha = git("rev-parse", "HEAD").stdout.strip()
    root = tmp_path / "www"; root.mkdir()
    for p, b in served.items():
        (root / p).parent.mkdir(parents=True, exist_ok=True); (root / p).write_bytes(b)
    dep = tmp_path / "deployed"; dep.write_text(f"{sha}\t2026-10-08T00:00:00Z\tok\n")
    bl = tmp_path / "baseline.json"; bl.write_text(json.dumps(baseline))
    return w.lines(str(root), str(repo), str(dep), str(bl))


BASE = {"generated": {"blog/index.html": "cron"}, "untracked": ["old.pdf"],
        "drift": {"card.json": hashlib.sha256(b"live").hexdigest()}}


def test_quiet_day_is_one_line_with_the_baseline_counted(tmp_path):
    out = _setup(tmp_path, {"index.html": b"a", "card.json": b"repo", "blog/index.html": b"x"},
                 {"index.html": b"a", "card.json": b"live", "old.pdf": b"p",
                  "blog/index.html": b"regenerated"}, BASE)
    assert len(out) == 1, out
    assert "0 neu ohne Commit, 0 neu abweichend" in out[0]
    assert "Ausgangsstand 1 ohne Commit, 1 abweichend" in out[0]


def test_a_file_only_in_the_web_root_is_named(tmp_path):
    out = _setup(tmp_path, {"index.html": b"a"},
                 {"index.html": b"a", "about.html.bak-1": b"old"}, BASE)
    assert "1 neu ohne Commit" in out[0]
    assert any(o.strip().startswith("ohne Commit: about.html.bak-1") for o in out)


def test_a_hand_edit_of_a_served_file_is_named(tmp_path):
    out = _setup(tmp_path, {"index.html": b"repo"}, {"index.html": b"edited on the server"}, BASE)
    assert "1 neu abweichend" in out[0]
    assert any(o.strip().startswith("weicht ab: index.html") for o in out)


def test_a_baselined_drift_that_changes_again_is_new(tmp_path):
    out = _setup(tmp_path, {"card.json": b"repo"}, {"card.json": b"changed once more"}, BASE)
    assert "1 neu abweichend" in out[0]


def test_generated_files_are_ignored(tmp_path):
    out = _setup(tmp_path, {"blog/index.html": b"x"}, {"blog/index.html": b"y"}, BASE)
    assert "0 neu ohne Commit, 0 neu abweichend" in out[0]


def test_an_unreadable_setup_is_a_line_not_silence(tmp_path):
    out = w.lines(str(tmp_path), str(tmp_path / "nope"), str(tmp_path / "nodeployed"),
                  str(tmp_path / "nobaseline"))
    assert len(out) == 1 and "nicht moeglich" in out[0]


def test_the_committed_baseline_parses():
    d = json.load(open(w.BASELINE, encoding="utf-8"))
    assert set(d["generated"]) == {"registry-proof.json", "blog/index.html"}
    assert len(d["untracked"]) == 31 and not any(".bak" in p for p in d["untracked"])
    assert len(d["drift"]) == 5
