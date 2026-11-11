"""docs/caep.html reaches the web root and stays current (ops W2, 14.10.2026).

Befund BR1: the web root served docs/caep.html as of 09.05.2026, one line
short of the committed page (the code-theme stylesheet added on 18.06.2026).
deploy.sh fenced off all of docs/, so the page never shipped, and
config/webroot_baseline.json listed the old bytes under "drift", so the 08:00
report counted it as known and said nothing.

Two probes, each with a firing and a silent case:

- web_sync_files names the page whenever the web root does not hold the
  checkout's bytes, so every web deploy ships it back;
- webroot_drift, with the committed baseline, reports an older copy in the web
  root as "weicht ab: docs/caep.html" instead of counting it.
"""
import json
import os
import pathlib
import subprocess

from scripts import webroot_drift as w

DEPLOY_SH = pathlib.Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh"
THEME = b'<link rel="stylesheet" href="/assets/css/code-theme.css?v=2">\n'
OLD = b"<html><head>\n<title>CAEP</title>\n</head><body>caep</body></html>\n"
NEW = OLD.replace(b"</head>", THEME + b"</head>")


def sync(checkout: pathlib.Path, root: pathlib.Path) -> subprocess.CompletedProcess:
    env = {"DEPLOY_SH_FUNCTIONS_ONLY": "1", "PATH": os.environ["PATH"], "WEB_ROOT": str(root)}
    return subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_SH}"; cd "{checkout}" && web_sync_files'],
        capture_output=True, text=True, env=env)


def tree(base: pathlib.Path, files: dict) -> pathlib.Path:
    for p, b in files.items():
        (base / p).parent.mkdir(parents=True, exist_ok=True)
        (base / p).write_bytes(b)
    return base


def test_an_older_caep_in_the_web_root_is_shipped_again(tmp_path):
    co = tree(tmp_path / "web", {"docs/caep.html": NEW})
    root = tree(tmp_path / "www", {"docs/caep.html": OLD})
    r = sync(co, root)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["docs/caep.html"]


def test_a_missing_caep_in_the_web_root_is_shipped(tmp_path):
    co = tree(tmp_path / "web", {"docs/caep.html": NEW})
    root = tmp_path / "www"; root.mkdir()
    assert sync(co, root).stdout.split() == ["docs/caep.html"]


def test_a_current_caep_ships_nothing(tmp_path):
    co = tree(tmp_path / "web", {"docs/caep.html": NEW, "docs/other.html": b"x"})
    root = tree(tmp_path / "www", {"docs/caep.html": NEW})
    r = sync(co, root)
    assert r.returncode == 0 and r.stdout == "", r.stdout + r.stderr


def test_the_web_deploy_ships_what_web_sync_files_names():
    src = DEPLOY_SH.read_text()
    body = src[src.index("deploy_web() {"):src.index("rollback() {")]
    assert 'files=$( { web_files "$PREV" "$SHA"; web_sync_files; } | sort -u)' in body


def _drift(tmp_path, served: bytes) -> list[str]:
    repo = tree(tmp_path / "repo", {"docs/caep.html": NEW, "index.html": b"a"})
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True,
                                    capture_output=True, text=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    git("add", "-A"); git("commit", "-q", "-m", "c")
    sha = git("rev-parse", "HEAD").stdout.strip()
    root = tree(tmp_path / "www", {"docs/caep.html": served, "index.html": b"a"})
    dep = tmp_path / "deployed"; dep.write_text(f"{sha}\t2026-10-14T00:00:00Z\tok\n")
    return w.lines(str(root), str(repo), str(dep), w.BASELINE)


def test_drift_names_an_older_caep_with_the_committed_baseline(tmp_path):
    out = _drift(tmp_path, OLD)
    assert "1 neu abweichend" in out[0], out
    assert any(o.strip().startswith("weicht ab: docs/caep.html") for o in out), out


def test_drift_is_quiet_when_caep_is_current(tmp_path):
    out = _drift(tmp_path, NEW)
    assert "0 neu abweichend" in out[0], out
    assert not any("docs/caep.html" in o for o in out), out


def test_the_committed_baseline_does_not_mask_caep():
    d = json.load(open(w.BASELINE, encoding="utf-8"))
    assert "docs/caep.html" not in d["drift"]
    assert "docs/caep.html" not in d["untracked"]
    assert "docs/caep.html" not in (d.get("generated") or {})


def test_the_invariant_reads_the_page_list_from_deploy_sh():
    # c-docs-pages-match-repo takes WEB_DOCS_PAGES out of deploy.sh with sed.
    # A reformatted assignment would leave the invariant with no pages and an
    # UNREADABLE line instead of a comparison.
    import yaml
    inv = yaml.safe_load((DEPLOY_SH.parents[2] / "docs" / "invariants"
                          / "c-docs-pages-match-repo.yaml").read_text())
    assert inv["schweregrad"] == "fail" and inv["tempo"] == "hourly"
    line = next(l for l in inv["abfrage"]["wert"].splitlines() if l.startswith("pages="))
    sed = line[line.index("sed -n ") + len("sed -n "):line.index(" /home/")]
    r = subprocess.run(["bash", "-c", f"sed -n {sed} \"{DEPLOY_SH}\""],
                       capture_output=True, text=True)
    assert r.stdout.split() == ["docs/caep.html"], r.stdout + r.stderr
