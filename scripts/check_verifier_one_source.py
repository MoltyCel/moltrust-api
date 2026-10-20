#!/usr/bin/env python3
"""Eine Quelle fuer den Verifier: ausgeliefert gegen den deployten Commit.

Der Pruefer, mit dem ein Fremder registry-proof.json nachrechnen soll, liegt
zweimal vor: als moltrust-api scripts/registry_proof.py — damit prueft der
Tageslauf, bevor er veroeffentlicht (scripts/registry_proof_publish.sh) — und
als moltrust-web registry-proof.py, und das ist die Datei, die ein Besucher
abruft. Nach D1 waren beide auseinander: die ausgelieferte Fassung meldete drei
Fehler gegen die Datei, die sie pruefen soll, siebzehn Tage lang, und keine
Pruefung hat es gesehen. scripts/webroot_drift.py vergleicht den Web-Root gegen
den deployten web-Commit; es haette auch dann geschwiegen, wenn beide Kopien
verschieden sind, weil es die api-Seite nicht kennt.

Diese Pruefung vergleicht, was ein Besucher abruft, mit dem Stand, mit dem der
Tageslauf prueft: sha256 der live geholten Datei gegen scripts/registry_proof.py
auf dem **deployten** api-Commit. Nicht gegen den Arbeitsbaum — der sagt nichts
darueber, was laeuft, und ein nicht committeter Handgriff im Checkout wuerde die
Pruefung gruen faerben.

Exit 0 = gleich, 1 = Unterschied, 2 = nicht pruefbar. Bei 2 steht UNREADABLE in
der ersten Zeile; scripts/selftest.py glaubt das und meldet FAIL, statt eine
Zahl zu vergleichen, die keine Messung ist.

Nicht im Umfang: die beiden Kopien zu einer zusammenlegen (ops W4).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys

import httpx

LIVE_URL = "https://moltrust.ch/registry-proof.py"
REPO = os.path.expanduser("~/moltstack")
DEPLOYED = os.path.expanduser("~/.deployed/moltrust-api")
PFAD = "scripts/registry_proof.py"
TIMEOUT = 30.0


class NichtPruefbar(Exception):
    """Die Messung konnte nicht gemacht werden. Kein Urteil, ein Befund."""


def deployter_commit(pfad: str = DEPLOYED) -> str:
    """Der SHA aus ~/.deployed/<repo>: erstes Feld der einen Zeile."""
    try:
        sha = open(pfad, encoding="utf-8").read().split("\t")[0].strip()
    except OSError as exc:
        raise NichtPruefbar(f"{pfad}: {type(exc).__name__}") from exc
    if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
        raise NichtPruefbar(f"{pfad} nennt keinen Commit: {sha[:20]!r}")
    return sha


def repo_fassung(repo: str, sha: str, pfad: str) -> bytes:
    """Die Bytes von <pfad> im Commit <sha>, aus dem Objektspeicher."""
    p = subprocess.run(["git", "-C", repo, "show", f"{sha}:{pfad}"],
                       capture_output=True, timeout=60)
    if p.returncode:
        raise NichtPruefbar(f"git show {sha[:7]}:{pfad}: "
                            f"{(p.stderr or b'').decode(errors='replace').strip()[:120]}")
    return p.stdout


def live_fassung(url: str, timeout: float = TIMEOUT) -> bytes:
    """Die Bytes, wie ein Besucher sie abruft. Keine Umleitung im Stillen."""
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=False,
                      headers={"User-Agent": "moltrust-invariant/w4"})
    except httpx.HTTPError as exc:
        raise NichtPruefbar(f"{url}: {type(exc).__name__}") from exc
    if r.status_code != 200:
        raise NichtPruefbar(f"{url}: HTTP {r.status_code}")
    return r.content


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def vergleich(live: bytes, repo: bytes, sha: str, pfad: str = PFAD) -> tuple[int, str]:
    if live == repo:
        return 0, f"gleich {_sha(live)[:12]}… / {len(live)} B @ {sha[:7]} {pfad}"
    return 1, (f"ABWEICHUNG ausgeliefert {_sha(live)[:12]}…/{len(live)} B, "
               f"{pfad} @ {sha[:7]} {_sha(repo)[:12]}…/{len(repo)} B")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=LIVE_URL)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--deployed", default=DEPLOYED)
    ap.add_argument("--pfad", default=PFAD)
    a = ap.parse_args(argv)
    try:
        sha = deployter_commit(a.deployed)
        repo = repo_fassung(a.repo, sha, a.pfad)
        live = live_fassung(a.url)
    except NichtPruefbar as exc:
        print(f"UNREADABLE {exc}")
        return 2
    rc, text = vergleich(live, repo, sha, a.pfad)
    print(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
