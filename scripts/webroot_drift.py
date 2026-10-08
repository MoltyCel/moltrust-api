"""Web-Root gegen Repo, fuer die 08:00-Sammelmeldung.

Am 08.10.2026 lagen 150 Dateien in /var/www/html, die nie durch einen Deploy
gegangen waren, 127 davon .bak-Kopien mit Status 200, darunter Sicherungen der
jwks und der Agent-Card. Niemand hatte sie gesehen, weil jede Pruefung vom Repo
aus schaute: was dort steht, wurde geprueft, was nur im Web-Root lag, nicht.

Zwei Zeilen, beide vom Web-Root aus:

- ohne Commit: Dateien, die im deployten moltrust-web-Commit nicht stehen;
- weicht ab: Dateien, die dort stehen, deren Bytes aber nicht die des Commits
  sind. Das faengt den Handgriff im Web-Root, der beim naechsten Deploy der
  Datei still verschwindet.

Was am 08.10. schon so lag, steht in config/webroot_baseline.json und wird nur
gezaehlt. Gemeldet wird, was dazukommt. Erzeugte Dateien (registry-proof.json,
blog/index.html) stehen dort unter "generated" und fallen aus beiden Zeilen.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pwd
import subprocess

WEB_ROOT = "/var/www/html"
WEB_REPO = os.path.expanduser("~/moltrust-web")
DEPLOYED = os.path.expanduser("~/.deployed/moltrust-web")
BASELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "webroot_baseline.json")
SKIP_DIRS = {".git"}
MAX_ITEMS = 10


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # nosec B324 - git object id, not security


def tracked_blobs(repo: str, sha: str) -> dict[str, str]:
    """path -> blob sha at the deployed commit."""
    out = subprocess.run(["git", "-C", repo, "ls-tree", "-r", sha],
                         capture_output=True, text=True, timeout=60, check=True).stdout
    blobs = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            blobs[path] = parts[2]
    return blobs


def _describe(root: str, rel: str) -> str:
    try:
        st = os.stat(os.path.join(root, rel))
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        when = dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)
        return f"{rel} ({st.st_size} B, {when:%d.%m.%Y %H:%M}Z, {owner})"
    except OSError as e:
        return f"{rel} (stat: {type(e).__name__})"


def scan(root: str, blobs: dict[str, str], baseline: dict) -> dict:
    generated = set(baseline.get("generated") or {})
    known_untracked = set(baseline.get("untracked") or [])
    known_drift = dict(baseline.get("drift") or {})
    new_untracked, new_drift, unreadable = [], [], []
    seen_untracked, seen_drift = 0, 0
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(d, name), root)
            if rel in generated:
                continue
            try:
                data = open(os.path.join(d, name), "rb").read()
            except OSError:
                unreadable.append(rel)
                continue
            if rel not in blobs:
                if rel in known_untracked:
                    seen_untracked += 1
                else:
                    new_untracked.append(rel)
                continue
            if git_blob_sha(data) == blobs[rel]:
                continue
            if known_drift.get(rel) == hashlib.sha256(data).hexdigest():
                seen_drift += 1
            else:
                new_drift.append(rel)
    return {"new_untracked": new_untracked, "new_drift": new_drift,
            "unreadable": unreadable, "known_untracked": seen_untracked,
            "known_drift": seen_drift}


def lines(root: str = WEB_ROOT, repo: str = WEB_REPO, deployed: str = DEPLOYED,
          baseline_path: str = BASELINE) -> list[str]:
    """Die Web-Root-Zeilen der Sammelmeldung. Immer mindestens eine."""
    try:
        sha = open(deployed).read().split("\t")[0].strip()
        baseline = json.load(open(baseline_path, encoding="utf-8"))
        blobs = tracked_blobs(repo, sha)
    except Exception as e:  # noqa: BLE001 - lieber eine Zeile mit Fehler als keine
        return [f"Web-Root: Pruefung nicht moeglich ({type(e).__name__}: {e})"]
    r = scan(root, blobs, baseline)
    head = (f"Web-Root gegen {sha[:7]} — {len(r['new_untracked'])} neu ohne Commit, "
            f"{len(r['new_drift'])} neu abweichend "
            f"(Ausgangsstand {r['known_untracked']} ohne Commit, "
            f"{r['known_drift']} abweichend)")
    out = [head]
    items = ([f"  ohne Commit: {_describe(root, p)}" for p in r["new_untracked"]]
             + [f"  weicht ab: {_describe(root, p)}" for p in r["new_drift"]]
             + [f"  nicht lesbar: {p}" for p in r["unreadable"]])
    out.extend(items[:MAX_ITEMS])
    if len(items) > MAX_ITEMS:
        out.append(f"  … und {len(items) - MAX_ITEMS} weitere")
    return out


if __name__ == "__main__":
    print("\n".join(lines()))
