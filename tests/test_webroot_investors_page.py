"""investors/index.html reaches the web root (ops INV-2, 23.11.2026).

The page lives in a folder, and WEB_ALLOW names folders one by one. Without
the line a merge of the page deploys green and serves nothing (the billing/
case, ops CHECKOUT-404). The page merges after this change, so the commit
range carries it and investors/ needs no WEB_SYNC_DIRS entry.

Probes: a range that adds the page names it; other files in the folder and
deeper index pages never ship.
"""
import os
import pathlib
import subprocess

DEPLOY_SH = pathlib.Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh"
PAGE = "investors/index.html"
OTHERS = ["investors/notes.md", "investors/draft.txt", "investors/x/index.html"]


def test_a_range_that_adds_the_page_ships_only_the_page(tmp_path):
    base = tmp_path / "web"
    git = lambda *a: subprocess.run(["git", "-C", str(base), *a], check=True,
                                    capture_output=True, text=True)
    base.mkdir()
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (base / "index.html").write_bytes(b"a")
    git("add", "-A"); git("commit", "-q", "-m", "before")
    before = git("rev-parse", "HEAD").stdout.strip()
    for p in [PAGE] + OTHERS:
        (base / p).parent.mkdir(parents=True, exist_ok=True)
        (base / p).write_bytes(p.encode())
    git("add", "-A"); git("commit", "-q", "-m", "page")
    after = git("rev-parse", "HEAD").stdout.strip()
    env = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"], "WEB_ROOT": str(tmp_path)}
    r = subprocess.run(["bash", "-c", f'source "{DEPLOY_SH}"; cd "{base}" && web_files {before} {after}'],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [PAGE]
