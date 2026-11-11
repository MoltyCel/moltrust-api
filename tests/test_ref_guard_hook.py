"""The ref guard writes what it sees (ops KQ3, 19.10.2026).

ops/deploy/ref-guard-hook runs here as git runs it on the server: installed as
the reference-transaction hook of a real repository, triggered by a commit on
main. HOME points at tmp_path, so the line lands in tmp_path/selftest and not
in the real log. A guard nobody knows to be writing is no guard; each case
here is one way it could stop writing without anyone noticing.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "ops" / "deploy" / "ref-guard-hook"

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("python3")),
                                reason="needs git and python3")


@pytest.fixture
def repo(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = tmp_path / "repo"
    env = {"PATH": os.environ["PATH"], "HOME": str(home),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], env=env, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "base"], cwd=r, env=env,
                   check=True)
    dst = r / ".git" / "hooks" / "reference-transaction"
    shutil.copy(HOOK, dst)
    dst.chmod(0o755)
    return r, home, env


def commit(repo, enforce_from, **extra):
    r, _, env = repo
    env = {**env, "REF_GUARD_ENFORCE_FROM": enforce_from, **extra}
    return subprocess.run(["git", "commit", "--allow-empty", "-m", "hand"], cwd=r, env=env,
                          capture_output=True, text=True)


def log_rows(repo):
    f = repo[1] / "selftest" / "ref-guard.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def head(repo):
    return subprocess.run(["git", "rev-parse", "main"], cwd=repo[0], env=repo[2],
                          capture_output=True, text=True).stdout.strip()


def test_a_hand_commit_on_main_lands_in_the_log(repo):
    p = commit(repo, "99999999")
    assert p.returncode == 0, p.stderr
    [row] = log_rows(repo)
    assert (row["mode"], row["refs"], row["cwd"]) == ("log", "refs/heads/main",
                                                       str(repo[0].resolve()))
    assert row["ts"] and "sid" in row


def test_a_refused_commit_lands_in_the_log_and_main_stays(repo):
    before = head(repo)
    p = commit(repo, "20000101")
    assert p.returncode != 0
    assert "ref guard: refused" in p.stderr
    assert [r["mode"] for r in log_rows(repo)] == ["refuse"]
    assert head(repo) == before


def test_the_deploy_passes_without_a_line(repo):
    p = commit(repo, "20000101", GIT_REFLOG_ACTION="deploy.sh moltrust-api abc123")
    assert p.returncode == 0, p.stderr
    assert log_rows(repo) == []


def test_another_branch_passes_without_a_line(repo):
    r, _, env = repo
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=r, env=env, check=True)
    p = commit(repo, "20000101")
    assert p.returncode == 0, p.stderr
    assert log_rows(repo) == []


def test_a_log_it_cannot_write_is_said_out_loud(repo):
    (repo[1] / "selftest").write_text("a file where the directory should be")
    p = commit(repo, "99999999")
    assert p.returncode == 0
    assert "ref guard: could not write ~/selftest/ref-guard.jsonl" in p.stderr
    assert "refs/heads/main is not recorded" in p.stderr
    assert "Error" in p.stderr          # the reason itself, no longer in /dev/null


def test_a_refusal_still_refuses_when_the_log_cannot_be_written(repo):
    (repo[1] / "selftest").write_text("a file where the directory should be")
    before = head(repo)
    p = commit(repo, "20000101")
    assert p.returncode != 0
    assert "could not write" in p.stderr and "ref guard: refused" in p.stderr
    assert head(repo) == before
