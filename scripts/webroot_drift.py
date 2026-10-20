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

Gerenderte Dateien stehen unter "rendered", mit ihrer Vorlage: deploy.sh
erzeugt sie bei jedem Web-Deploy im Checkout und installiert sie von dort
(.well-known/x402.json aus .well-known/x402.json.tpl, S2). Sie sind kein
Commit-Blob und doch nicht frei: die Vorlage muss im deployten Commit stehen,
und die ausgelieferten Bytes muessen die der Render-Ausgabe im Checkout sein.
Bis 14.10.2026 (ops W1, Befund BR3) zaehlte x402.json hier als "ohne Commit".
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


def rendered_output(repo: str, rel: str) -> bytes | None:
    """Die Render-Ausgabe, die deploy.sh im Checkout abgelegt hat."""
    try:
        return open(os.path.join(repo, rel), "rb").read()
    except OSError:
        return None


def scan(root: str, blobs: dict[str, str], baseline: dict, repo: str | None = None) -> dict:
    generated = set(baseline.get("generated") or {})
    rendered = dict(baseline.get("rendered") or {})
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
            if rel in rendered:
                tpl = rendered[rel]
                out = rendered_output(repo, rel) if repo else None
                if tpl not in blobs or out != data:
                    new_drift.append(rel)
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


def baseline_hinweis(baseline: dict) -> str:
    """Der Zusatz in der Kopfzeile: woher der Ausgangsstand begruendet ist.

    Bis zum 12.10.2026 stand dort nur "Ausgangsstand 27 ohne Commit, 3
    abweichend" — zwei Zahlen ohne Namen, und wer wissen wollte, was das ist,
    musste die Datei aufschlagen. Jetzt nennt die Zeile die Zahl der Gruppen
    und die Datei; die Namen stehen dort, begruendet je Gruppe und je Pfad.
    """
    b = baseline.get("begruendung") or {}
    if not b:
        return ""
    gruppen = b.get("ohne_commit") or []
    abw = b.get("abweichend") or []
    return (f", begruendet in config/webroot_baseline.json "
            f"({len(gruppen)} Gruppen, {len(abw)} Pfade)")


def baseline_lines(baseline: dict) -> list[str]:
    """Eigene Zeilen nur, wenn die Begruendung fehlt oder nicht aufgeht.

    Ein ruhiger Tag bleibt eine Zeile. Mein erster Entwurf haengte die
    Gruppen an jede Meldung; `test_quiet_day_is_one_line_with_the_baseline_
    counted` hat das abgewiesen, und zurecht — ein statischer Umstand, der
    zweimal taeglich zwei Zeilen kostet, wird nach einer Woche nicht mehr
    gelesen. Hier bleibt also nur das Fehlen laut.
    """
    b = baseline.get("begruendung") or {}
    if not b:
        return ["  Ausgangsstand ohne Begruendung — "
                "config/webroot_baseline.json traegt keinen "
                "begruendung-Block"]
    # Geht die Begruendung nicht auf, ist sie keine. Eine Gruppensumme, die
    # von der gezaehlten Zahl abweicht, ist genau die Sorte Angabe, die
    # vollstaendig aussieht und es nicht ist.
    summe = sum(int(g.get("zahl") or 0) for g in b.get("ohne_commit") or [])
    gezaehlt = len(baseline.get("untracked") or [])
    raus = []
    if summe and gezaehlt and summe > gezaehlt:
        raus.append(f"  Begruendung geht nicht auf: {summe} begruendet, "
                    f"{gezaehlt} im Ausgangsstand")
    fehlt = [g.get("gruppe", "?") for g in b.get("ohne_commit") or []
             if not g.get("grund")]
    fehlt += [a.get("pfad", "?") for a in b.get("abweichend") or []
              if not a.get("grund")]
    if fehlt:
        raus.append("  ohne Grund im Ausgangsstand: " + ", ".join(fehlt))
    return raus


def lines(root: str = WEB_ROOT, repo: str = WEB_REPO, deployed: str = DEPLOYED,
          baseline_path: str = BASELINE) -> list[str]:
    """Die Web-Root-Zeilen der Sammelmeldung. Immer mindestens eine."""
    try:
        sha = open(deployed).read().split("\t")[0].strip()
        baseline = json.load(open(baseline_path, encoding="utf-8"))
        blobs = tracked_blobs(repo, sha)
    except Exception as e:  # noqa: BLE001 - lieber eine Zeile mit Fehler als keine
        return [f"Web-Root: Pruefung nicht moeglich ({type(e).__name__}: {e})"]
    r = scan(root, blobs, baseline, repo)
    head = (f"Web-Root gegen {sha[:7]} — {len(r['new_untracked'])} neu ohne Commit, "
            f"{len(r['new_drift'])} neu abweichend "
            f"(Ausgangsstand {r['known_untracked']} ohne Commit, "
            f"{r['known_drift']} abweichend"
            + baseline_hinweis(baseline) + ")")
    out = [head]
    # Der Ausgangsstand steht nicht als nackte Zahl. Bis zum 12.10.2026 hiess
    # es "27 ohne Commit, 3 abweichend", und wer wissen wollte, was das ist,
    # musste die Baseline-Datei aufschlagen. Eine Zahl ohne Namen verschiebt
    # die Arbeit nur.
    out.extend(baseline_lines(baseline))
    items = ([f"  ohne Commit: {_describe(root, p)}" for p in r["new_untracked"]]
             + [f"  weicht ab: {_describe(root, p)}" for p in r["new_drift"]]
             + [f"  nicht lesbar: {p}" for p in r["unreadable"]])
    out.extend(items[:MAX_ITEMS])
    if len(items) > MAX_ITEMS:
        out.append(f"  … und {len(items) - MAX_ITEMS} weitere")
    return out


if __name__ == "__main__":
    print("\n".join(lines()))
