"""Meldet jeden Schreibvorgang in den Web-Root, der nicht von deploy.sh kommt.

Am 12.10.2026 lagen zwei Dateien im Web-Root, die keine Sitzung verbucht hatte
(Selftest-Befunde BR2, BR3; ops W1). Die Herkunft stand nur im sudo-Journal:
registry-proof.py kam am 27.09. um 17:22:15 UTC per Hand-`install` aus
~/blog-deploy-stage, und kein Deploy konnte die Datei ausliefern, weil
web_files() keine .py kennt. Nach D1 meldete genau dieser Verifier drei Fehler
gegen die Datei, die er pruefen soll, und niemand hatte ihn seit 17 Tagen
angesehen. scripts/webroot_drift.py sieht so etwas nur, wenn die Bytes vom Repo
abweichen, und nur zweimal am Tag; den Schreibvorgang selbst sieht es nicht.

Diese Wache schaut auf den Vorgang, nicht auf den Zustand. Alle fuenf Minuten:

- jede Datei im Web-Root, deren ctime nach dem letzten Lauf liegt, ist ein
  Schreibvorgang. ctime, nicht mtime: `touch -d` setzt mtime zurueck, ctime
  nicht;
- deploy.sh schreibt fuer jede installierte Datei eine Zeile in
  ~/selftest/webroot-writes.jsonl (Pfad, sha256, Zeit). Ein Schreibvorgang
  gilt als Deploy, wenn eine Zeile mit demselben Pfad und denselben Bytes
  hoechstens FENSTER Sekunden neben der ctime steht;
- alles andere geht sofort ueber app/notify.py nach ALERTS, mit Pfad und
  Zeitpunkt.

Ausgenommen sind die erzeugten Dateien aus config/webroot_baseline.json
("generated"): blog/index.html schreibt der root-Cron alle 15 Minuten neu,
registry-proof.json der Tageslauf. Beide haben einen erklaerten Schreiber
(e-one-writer-per-artefact); sie hier zu melden hiesse 97 Meldungen am Tag,
und nach der dritten liest niemand mehr die vierte.

Grenzen: geloeschte Dateien sieht die Wache nicht (keine ctime mehr), ein
Verzeichnis .git im Web-Root wird wie in webroot_drift.py uebergangen, und der
erste Lauf ohne Zustand setzt nur den Stand und meldet nichts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pwd
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

WEB_ROOT = "/var/www/html"
LEDGER = os.path.expanduser("~/selftest/webroot-writes.jsonl")
STATE = os.path.expanduser("~/selftest/webroot-write-watch.json")
BASELINE = os.path.join(ROOT_DIR, "config", "webroot_baseline.json")
SKIP_DIRS = {".git"}
# Zwischen dem Install und der Ledger-Zeile liegen ein sha256sum und ein
# printf, gemessen unter einer Sekunde. Das Fenster ist grosszuegig, weil ein
# zu enges einen Deploy als Fremdschreiber meldet; ein Fremder muesste dafuer
# dieselben Bytes in dasselbe Fenster schreiben.
FENSTER = 120
MAX_ITEMS = 10


def _sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def load_ledger(path: str) -> dict[str, list[tuple[float, str]]]:
    """path -> [(epoch, sha256)] aus der Ledger-Datei von deploy.sh."""
    out: dict[str, list[tuple[float, str]]] = {}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
            if row.get("writer") != "deploy.sh":
                continue
            ts = dt.datetime.strptime(row["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=dt.timezone.utc).timestamp()
            out.setdefault(row["path"], []).append((ts, row["sha256"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def von_deploy(rel: str, ctime: float, digest: str | None, ledger: dict) -> bool:
    return any(abs(ts - ctime) <= FENSTER and digest == sha
               for ts, sha in ledger.get(rel, []))


def writes_since(root: str, cursor: float, generated: set[str]) -> list[tuple[str, float]]:
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(d, name)
            rel = os.path.relpath(full, root)
            if rel in generated:
                continue
            try:
                ct = os.lstat(full).st_ctime
            except OSError:
                continue
            if ct > cursor:
                found.append((rel, ct))
    return found


def _owner(path: str) -> str:
    try:
        uid = os.lstat(path).st_uid
    except OSError:
        return "?"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def scan(root: str, ledger_path: str, state_path: str, baseline_path: str,
         now: float | None = None) -> tuple[list[str], dict]:
    """Gibt (Meldezeilen, neuer Zustand) zurueck. Leere Liste = still."""
    now = time.time() if now is None else now
    try:
        state = json.load(open(state_path, encoding="utf-8"))
        cursor = float(state["cursor"])
    except FileNotFoundError:
        return [], {"cursor": now, "init": True}
    baseline = json.load(open(baseline_path, encoding="utf-8"))
    generated = set(baseline.get("generated") or {})
    ledger = load_ledger(ledger_path)
    fremd = []
    for rel, ct in writes_since(root, cursor, generated):
        full = os.path.join(root, rel)
        if von_deploy(rel, ct, _sha256(full), ledger):
            continue
        when = dt.datetime.fromtimestamp(ct, dt.timezone.utc)
        size = os.lstat(full).st_size if os.path.lexists(full) else -1
        fremd.append(f"  {rel} — {when:%d.%m.%Y %H:%M:%S}Z, {size} B, {_owner(full)}")
    # Der Stand ist der Beginn dieses Laufs: was waehrend des Durchgangs
    # geschrieben wird, sieht der naechste Lauf noch einmal, statt dass es
    # zwischen zwei Laeufen verloren geht.
    new_state = {"cursor": now, "zuletzt": dt.datetime.fromtimestamp(
        now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "gemeldet": len(fremd)}
    if not fremd:
        return [], new_state
    head = (f"Web-Root: {len(fremd)} Schreibvorgang/-vorgaenge ausserhalb deploy.sh "
            f"in {root}")
    body = fremd[:MAX_ITEMS]
    if len(fremd) > MAX_ITEMS:
        body.append(f"  … und {len(fremd) - MAX_ITEMS} weitere")
    return [head, *body, "Herkunft pruefen: sudo-Journal, ~/logs/deploy.log, "
            "~/selftest/webroot-writes.jsonl"], new_state


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=WEB_ROOT)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--channel", default="alerts")
    a = ap.parse_args(argv)
    lines, state = scan(a.root, a.ledger, a.state, a.baseline)
    if state.get("init"):
        state.pop("init")
        _save_state(a.state, {**state, "zuletzt": dt.datetime.fromtimestamp(
            state["cursor"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "gemeldet": 0})
        print("webroot-write-watch: erster Lauf, Stand gesetzt, nichts gemeldet")
        return 0
    if lines:
        from app import notify
        text = "\n".join(lines)
        print(text)
        if not notify.send_telegram(text, channel=a.channel):
            # Der Stand bleibt stehen: der naechste Lauf findet dieselben
            # Schreibvorgaenge und versucht es noch einmal.
            print("webroot-write-watch: Meldung nicht zugestellt, Stand unveraendert",
                  file=sys.stderr)
            return 1
    else:
        print(f"webroot-write-watch: still seit {state['zuletzt']}")
    _save_state(a.state, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
