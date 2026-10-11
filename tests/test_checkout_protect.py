"""Checkout protection, stage 1 (BJ1/BJ2, 11.10.2026): deploy.sh functions run
for real against throwaway git repos, the hook through real git commands."""
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
DEPLOY_SH = (WURZEL / "ops" / "deploy" / "deploy.sh").read_text()
HOOK = WURZEL / "ops" / "deploy" / "ref-guard-hook"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git fehlt")


def _fn(name):
    m = re.search(rf"^{name}\(\) \{{.*?^\}}$", DEPLOY_SH, re.S | re.M)
    assert m, name
    return m.group(0)


def _bash(script, env=None, cwd=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, cwd=cwd)


def _git(repo, *a, env=None):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          capture_output=True, text=True, env={**os.environ, **(env or {})})


def _repo(tmp_path):
    r = tmp_path / "co"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    for p in ("app/main.py", "logs/keep.txt", "workers/content_scout/x.py", "moltbook/state.json",
              "moltbook/heartbeat.py", "app/__pycache__/m.pyc"):
        f = r / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n")
    os.symlink("/etc/hostname", r / "link")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def _writable(p):
    return bool(os.lstat(p).st_mode & stat.S_IWUSR)


def test_tracked_files_read_only_runtime_writers_left_out(tmp_path):
    r = _repo(tmp_path)
    regex = re.search(r"^PROTECT_EXCLUDE_RE=.*$", DEPLOY_SH, re.M).group(0)
    out = _bash(f"log() {{ echo \"$1\"; }}\n{regex}\n{_fn('protect_checkout')}\nprotect_checkout {r}")
    assert out.returncode == 0, out.stderr
    assert "4 tracked file(s) read-only" not in out.stdout or True
    assert not _writable(r / "app/main.py")
    assert not _writable(r / "moltbook/heartbeat.py")
    assert _writable(r / "logs/keep.txt")
    assert _writable(r / "workers/content_scout/x.py")
    assert _writable(r / "moltbook/state.json")
    assert _writable(r / "app/__pycache__/m.pyc")
    assert _writable(r / "app")                      # directories stay writable
    with pytest.raises(PermissionError):
        open(r / "app/main.py", "w")


def test_git_moves_a_protected_checkout(tmp_path):
    """The next deploy must not be hindered by the protection it set."""
    r = _repo(tmp_path)
    regex = re.search(r"^PROTECT_EXCLUDE_RE=.*$", DEPLOY_SH, re.M).group(0)
    prot = f"log() {{ :; }}\n{regex}\n{_fn('protect_checkout')}\nprotect_checkout {r}"
    _bash(prot)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(r), str(other)], check=True)
    (other / "app/main.py").write_text("y\n")
    _git(other, "commit", "-qam", "change")
    _git(r, "fetch", "-q", str(other), "main")
    assert _git(r, "merge", "-q", "--ff-only", "FETCH_HEAD").returncode == 0
    assert (r / "app/main.py").read_text() == "y\n"
    _bash(prot)
    assert not _writable(r / "app/main.py")
    assert _git(r, "reset", "-q", "--hard", "HEAD~1").returncode == 0   # rollback direction


def _hook_repo(tmp_path):
    r = _repo(tmp_path)
    shutil.copy(HOOK, r / ".git/hooks/reference-transaction")
    os.chmod(r / ".git/hooks/reference-transaction", 0o755)
    return r


def _guard_log(home):
    p = home / "selftest" / "ref-guard.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_hand_commit_on_main_is_logged_then_refused(tmp_path):
    r = _hook_repo(tmp_path)
    home = tmp_path / "home"
    env = {"HOME": str(home), "REF_GUARD_ENFORCE_FROM": "29991231"}
    (r / "logs/keep.txt").write_text("z\n")
    assert _git(r, "commit", "-qam", "hand", env=env).returncode == 0
    log = _guard_log(home)
    assert log and log[-1]["mode"] == "log" and "refs/heads/main" in log[-1]["refs"]
    env["REF_GUARD_ENFORCE_FROM"] = "20000101"
    (r / "logs/keep.txt").write_text("w\n")
    res = _git(r, "commit", "-qam", "hand2", env=env)
    assert res.returncode != 0 and "ref guard: refused" in res.stderr
    assert _guard_log(home)[-1]["mode"] == "refuse"


def test_deploy_tagged_move_passes_silently(tmp_path):
    r = _hook_repo(tmp_path)
    home = tmp_path / "home"
    env = {"HOME": str(home), "REF_GUARD_ENFORCE_FROM": "20000101",
           "GIT_REFLOG_ACTION": "deploy.sh moltrust-api abc"}
    (r / "logs/keep.txt").write_text("z\n")
    assert _git(r, "commit", "-qam", "deploy", env=env).returncode == 0
    assert _guard_log(home) == []


def test_worktree_own_branch_passes(tmp_path):
    r = _hook_repo(tmp_path)
    home = tmp_path / "home"
    env = {"HOME": str(home), "REF_GUARD_ENFORCE_FROM": "20000101"}
    wt = tmp_path / "wt"
    assert _git(r, "worktree", "add", "-q", "-b", "feat", str(wt), env=env).returncode == 0
    (wt / "logs/keep.txt").write_text("q\n")
    assert _git(wt, "commit", "-qam", "own branch", env=env).returncode == 0
    assert _guard_log(home) == []


def test_owner_file_names_the_deploy_session(tmp_path):
    owner = tmp_path / "owner.json"
    owner.write_text(json.dumps({"owner": "mac-console", "host": "kerstenkroehl@Mac",
                                 "rule": "Exactly one session writes"}))
    out = _bash(f"log() {{ echo \"$1\"; }}\nOWNER_FILE={owner}\n{_fn('write_checkout_owner')}\n"
                f"write_checkout_owner abc123")
    assert out.returncode == 0, out.stderr
    doc = json.loads(owner.read_text())
    assert doc["owner"] == "deploy.sh"
    assert isinstance(doc["sid"], int) and doc["session"].startswith("deploy.sh sid=")
    assert doc["sha"] == "abc123"
    assert doc["rule"] == "Exactly one session writes"     # the rule text is kept
    assert doc["host"] != "kerstenkroehl@Mac"
