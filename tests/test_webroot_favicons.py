"""The favicon set reaches the web root (ops FAV-1, 26.11.2026).

moltrust-web adds favicon-48x48.png, icon-192.png and icon-512.png next to the
existing icons. WEB_ALLOW names the root icon files one by one; without the
entries the web merge deploys green and the new sizes answer 404.

Probe: a range that adds the icons names exactly them; other root PNGs never ship.
"""
import os
import pathlib
import subprocess

DEPLOY_SH = pathlib.Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh"
ICONS = ["favicon-16x16.png", "favicon-32x32.png", "favicon-48x48.png",
         "icon-192.png", "icon-512.png", "apple-touch-icon.png", "favicon.ico", "favicon.svg"]
OTHERS = ["icon-1024.png", "notes.png", "img2/icon-192.png"]


def test_a_range_that_adds_the_icons_ships_only_the_icons(tmp_path):
    base = tmp_path / "web"
    git = lambda *a: subprocess.run(["git", "-C", str(base), *a], check=True,
                                    capture_output=True, text=True)
    base.mkdir()
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (base / "index.html").write_bytes(b"a")
    git("add", "-A"); git("commit", "-q", "-m", "before")
    before = git("rev-parse", "HEAD").stdout.strip()
    for p in ICONS + OTHERS:
        (base / p).parent.mkdir(parents=True, exist_ok=True)
        (base / p).write_bytes(p.encode())
    git("add", "-A"); git("commit", "-q", "-m", "icons")
    after = git("rev-parse", "HEAD").stdout.strip()
    env = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"], "WEB_ROOT": str(tmp_path)}
    r = subprocess.run(["bash", "-c", f'source "{DEPLOY_SH}"; cd "{base}" && web_files {before} {after}'],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.split()) == sorted(ICONS)
