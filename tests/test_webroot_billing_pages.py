"""The Stripe return pages reach the web root (ops CHECKOUT-404, 04.11.2026).

moltrust-web#399 added billing/success/index.html and billing/cancel/index.html.
The deploy of that merge was green and logged "nothing served changed":
WEB_ALLOW did not name billing/, so both URLs stayed on the nginx 404 after a
paid checkout. The merge is already behind the recorded commit, so billing/ is
also one of WEB_SYNC_DIRS and the next web deploy ships the pages.

Probes: a range that adds the pages names both; web_sync_files names both
while the web root lacks them and nothing once it holds them; other files
under billing/ never ship.
"""
import os
import pathlib
import subprocess

DEPLOY_SH = pathlib.Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh"
PAGES = ["billing/cancel/index.html", "billing/success/index.html"]


def run(checkout: pathlib.Path, root: pathlib.Path, call: str) -> subprocess.CompletedProcess:
    env = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"], "WEB_ROOT": str(root)}
    return subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SH}"; cd "{checkout}" && {call}'],
        capture_output=True, text=True, env=env)


def repo(base: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    git = lambda *a: subprocess.run(["git", "-C", str(base), *a], check=True,
                                    capture_output=True, text=True)
    base.mkdir()
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (base / "index.html").write_bytes(b"a")
    git("add", "-A"); git("commit", "-q", "-m", "before")
    before = git("rev-parse", "HEAD").stdout.strip()
    for p in PAGES + ["billing/notes.md", "billing/success/draft.txt"]:
        (base / p).parent.mkdir(parents=True, exist_ok=True)
        (base / p).write_bytes(p.encode())
    git("add", "-A"); git("commit", "-q", "-m", "billing")
    return base, before, git("rev-parse", "HEAD").stdout.strip()


def test_a_range_that_adds_the_pages_ships_both(tmp_path):
    co, before, after = repo(tmp_path / "web")
    r = run(co, tmp_path, f"web_files {before} {after}")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == PAGES


def test_pages_missing_from_the_web_root_are_shipped(tmp_path):
    co, _, _ = repo(tmp_path / "web")
    root = tmp_path / "www"; root.mkdir()
    r = run(co, root, "web_sync_files")
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == PAGES


def test_current_pages_ship_nothing(tmp_path):
    co, _, _ = repo(tmp_path / "web")
    root = tmp_path / "www"
    for p in PAGES:
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_bytes((co / p).read_bytes())
    r = run(co, root, "web_sync_files")
    assert r.returncode == 0 and r.stdout == "", r.stdout + r.stderr
