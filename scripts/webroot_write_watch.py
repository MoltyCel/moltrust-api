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

Die erzeugten Dateien aus config/webroot_baseline.json ("generated") sind nicht
ausgenommen. blog/index.html schreibt der root-Cron alle 15 Minuten neu,
registry-proof.json der Tageslauf — zusammen 97 Schreibvorgaenge am Tag, und
eine Meldung je Vorgang hiesse, dass nach der dritten niemand mehr die vierte
liest. Still bleibt deshalb nicht der Pfad, sondern der Inhalt: ein Vorgang an
einer erzeugten Datei schweigt nur, wenn die gelieferten Bytes die Ausgabe ihres
Generators sind. Eine Ausnahme nach Pfad wuerde einen fremden Schreiber in genau
den beiden Dateien verdecken, die im oeffentlichen Web-Root am haeufigsten
beschrieben werden.

- blog/index.html: der Generator wird selbst gefahren, auf einem Sandkasten aus
  Symlinks auf die Beitraege und einer Kopie der gelieferten Datei. Es laeuft
  also das Programm, das der root-Cron fuehrt, nicht eine zweite Nachbildung
  seiner Logik. Gemessen am 14.10.2026 (a): zwei Laeufe auf einer Kopie des
  Web-Roots ergeben dieselben Bytes wie die gelieferte Datei
  (d70c573f…); die Ausgabe traegt keinen Zeitstempel, es ist also keiner zu
  normalisieren. Dazu der Rahmen: der Generator nimmt alles ausserhalb der
  Beitragsliste aus der gelieferten Datei und ist darin blind, ein eingefuegtes
  script-Element waere ein Festpunkt. Der Rahmen wird deshalb gegen
  blog/index.html am deployten moltrust-web-Commit geprueft (ops WRW-RAHMEN).
- registry-proof.json: scripts/registry_proof_export.py erzeugt die Datei neu,
  mit --as-of aus der gelieferten Datei, und der Vergleich nimmt das eine
  Zeitstempelfeld generated_at namentlich heraus. Gemessen am 14.10.2026 (a):
  Neuerzeugung und gelieferte Datei sind ohne dieses Feld gleich
  (53eae53c…), 526 Zeilen.

Grenzen: geloeschte Dateien sieht die Wache nicht (keine ctime mehr), ein
Verzeichnis .git im Web-Root wird wie in webroot_drift.py uebergangen, und der
erste Lauf ohne Zustand setzt nur den Stand und meldet nichts. Laesst eine
Pruefung sich nicht fahren, ist das ein Befund und kein Schweigen.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
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
# Kennung des Befunds fuer notify.send_befunde: der Fingerabdruck der
# Drosselung haengt am Text, die Kennung steht in der Rueckgabe.
KENNUNG = "webroot-write-watch"
GEN_BLOG_INDEX = os.path.join(ROOT_DIR, "scripts", "generate_blog_index.py")
GEN_REGISTRY_PROOF = os.path.join(ROOT_DIR, "scripts", "registry_proof_export.py")
# Der Export fragt die Datenbank; dasselbe Zeitlimit wie app/registry_export.
EXPORT_TIMEOUT = 300
# Namentlich, nicht per Muster ueber die Datei: ein Regex ueber JSON wuerde
# jeden Zeitstempel in jeder Zeile mitnehmen und damit auch einen, der zur
# Aussage der Datei gehoert.
ZEITSTEMPELFELD = "generated_at"
# Der Rahmen von blog/index.html: alles ausserhalb der Beitragsliste. Der
# Generator nimmt ihn aus der gelieferten Datei und ist darin blind — ein
# Handgriff ausserhalb der Liste waere wieder ein Festpunkt. Die Erwartung ist
# der Rahmen von blog/index.html am deployten moltrust-web-Commit, gelesen bei
# jeder Pruefung (ops WRW-RAHMEN, Gate 6315861227, Lars 24.10.2026).
# Bis dahin stand hier ein fester Wert, BLOG_INDEX_RAHMEN. Er musste nach jeder
# Nav-Aenderung per PR nachgezogen werden, und dazwischen meldete die Wache die
# Aenderung als fremden Eingriff: am 14.10. auf 907984982d64…, am 21.10. auf
# 6b1f0263de2f… (WRW-2 Kriterium 4).
# Der Commit steht in der ersten Spalte von ~/.deployed/moltrust-web, die
# deploy.sh nach jedem Lauf schreibt, auch nach einem Rueckfall — sie nennt
# also, was ausgeliefert ist. Gelesen wird aus dem Checkout, den der Deploy
# auf diesen Commit stellt.
WEB_DEPLOYED = os.path.expanduser("~/.deployed/moltrust-web")
WEB_CHECKOUT = os.path.expanduser("~/moltrust-web")
GIT_TIMEOUT = 30


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


def _kanonisch(doc: dict) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def _lade_generator(pfad: str, name: str):
    """Den Generator als Modul laden. Modulebene ohne Wirkung, nur Konstanten."""
    spec = importlib.util.spec_from_file_location(name, pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def erwartete_blog_index(full: str, generator: str = GEN_BLOG_INDEX) -> tuple[bytes | None, str]:
    """(Bytes des Generators, Grund) — Bytes None heisst: nicht gelaufen.

    Der Sandkasten verlinkt die Beitraege und kopiert nur die Indexdatei, damit
    der Generator seine Vorlage aus der gelieferten Datei nimmt und dabei nichts
    im Web-Root anfasst.
    """
    blog_dir = os.path.dirname(os.path.abspath(full))
    basis = os.path.basename(full)
    sand = tempfile.mkdtemp(prefix="webroot-write-watch-")
    try:
        for name in os.listdir(blog_dir):
            if name.endswith(".html") and name != basis:
                os.symlink(os.path.join(blog_dir, name), os.path.join(sand, name))
        ziel = os.path.join(sand, basis)
        shutil.copyfile(full, ziel)
        gen = _lade_generator(generator, "blog_index_generator_probe")
        gen.BLOG_DIR = sand
        gen.INDEX_FILE = ziel
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            gen.main()
        ausgabe = puffer.getvalue().strip()
        if "regenerated with" not in ausgabe:
            letzte = ausgabe.splitlines()[-1] if ausgabe else "keine Ausgabe"
            return None, letzte[:120]
        return open(ziel, "rb").read(), ""
    finally:
        shutil.rmtree(sand, ignore_errors=True)


def rahmen_sha256(text: str, generator: str = GEN_BLOG_INDEX) -> str | None:
    """sha256 des Rahmens, mit den Markern des Generators geschnitten."""
    gen = _lade_generator(generator, "blog_index_generator_marker")
    i = text.find(gen.TEMPLATE_BEFORE_MARKER)
    if i < 0:
        return None
    i += len(gen.TEMPLATE_BEFORE_MARKER)
    j = text.find(gen.TEMPLATE_AFTER_MARKER, i)
    if j < 0:
        return None
    return hashlib.sha256((text[:i] + text[j:]).encode()).hexdigest()


def erwarteter_rahmen(deployed: str = WEB_DEPLOYED,
                      checkout: str = WEB_CHECKOUT) -> tuple[str | None, str]:
    """(sha256 des Rahmens, Commit) aus dem deployten moltrust-web, oder
    (None, Grund). Kein Rueckfall auf einen festen Wert: eine Erwartung, die
    sich nicht bilden laesst, ist ein Befund."""
    try:
        with open(deployed, encoding="utf-8") as f:
            sha = f.readline().split("\t", 1)[0].strip()
    except OSError as e:
        return None, f"kein deployter moltrust-web-Commit ({type(e).__name__} {deployed})"
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        return None, f"kein Commit in {deployed}"
    try:
        p = subprocess.run(["git", "-C", checkout, "show", f"{sha}:blog/index.html"],
                           capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git show am Commit {sha[:7]}: {type(e).__name__}"
    if p.returncode != 0:
        return None, f"blog/index.html am deployten moltrust-web {sha[:7]} nicht lesbar"
    rahmen = rahmen_sha256(p.stdout.decode("utf-8", "replace"))
    if rahmen is None:
        return None, f"Marker der Beitragsliste fehlt in blog/index.html am Commit {sha[:7]}"
    return rahmen, sha


def pruefe_blog_index(full: str, rahmen: str | None = None,
                      deployed: str = WEB_DEPLOYED,
                      checkout: str = WEB_CHECKOUT) -> str | None:
    """None = die gelieferte Datei ist die Ausgabe ihres Generators.

    Zwei Teile, weil der Generator nur einen davon bestimmt: der Rahmen gegen
    blog/index.html am deployten moltrust-web-Commit, die Beitragsliste gegen
    den Lauf des Generators selbst. `rahmen` gesetzt heisst: diese Erwartung
    statt der aus dem Commit (Proben).
    """
    herkunft = ""
    if rahmen is None:
        rahmen, quelle = erwarteter_rahmen(deployed, checkout)
        if rahmen is None:
            return f"nicht pruefbar: {quelle}"
        herkunft = f" aus moltrust-web {quelle[:7]}"
    try:
        live = open(full, "rb").read()
    except OSError as e:
        return f"nicht pruefbar: {type(e).__name__}"
    try:
        ist = rahmen_sha256(live.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001 - ein Befund, kein Abbruch der Wache
        return f"nicht pruefbar: Rahmen {type(e).__name__}: {str(e)[:120]}"
    if ist is None:
        return "nicht pruefbar: Marker der Beitragsliste fehlt"
    if ist != rahmen:
        return (f"Rahmen weicht von der Erwartung ab (sha256 {ist[:12]}…, "
                f"erwartet {rahmen[:12]}…{herkunft})")
    try:
        erwartet, grund = erwartete_blog_index(full)
    except Exception as e:  # noqa: BLE001 - ein Befund, kein Abbruch der Wache
        return f"nicht pruefbar: Generator {type(e).__name__}: {str(e)[:120]}"
    if erwartet is None:
        return f"nicht pruefbar: Generator meldet {grund!r}"
    if erwartet == live:
        return None
    return (f"weicht von der Generatorausgabe ab (erwartet sha256 "
            f"{hashlib.sha256(erwartet).hexdigest()[:12]}…/{len(erwartet)} B, "
            f"geliefert {hashlib.sha256(live).hexdigest()[:12]}…/{len(live)} B)")


def _export_registry_proof(out: str, as_of: str) -> None:
    subprocess.run([sys.executable, GEN_REGISTRY_PROOF, "--out", out, "--as-of", as_of],
                   cwd=ROOT_DIR, capture_output=True, text=True,
                   timeout=EXPORT_TIMEOUT, check=True)


def pruefe_registry_proof(full: str, export=None) -> str | None:
    """None = gleich der Neuerzeugung, bis auf das eine Zeitstempelfeld."""
    try:
        live = json.load(open(full, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"nicht pruefbar: {type(e).__name__}"
    as_of = live.get("as_of") if isinstance(live, dict) else None
    if not as_of:
        return "nicht pruefbar: kein as_of in der gelieferten Datei"
    sand = tempfile.mkdtemp(prefix="webroot-write-watch-")
    try:
        neu_pfad = os.path.join(sand, os.path.basename(full))
        (export or _export_registry_proof)(neu_pfad, as_of)
        neu = json.load(open(neu_pfad, encoding="utf-8"))
    except subprocess.CalledProcessError as e:
        kurz = (e.stderr or e.stdout or "").strip().splitlines()
        return f"nicht pruefbar: Neuerzeugung Exit {e.returncode}: {(kurz[-1] if kurz else '')[:120]}"
    except Exception as e:  # noqa: BLE001 - ein Befund, kein Abbruch der Wache
        return f"nicht pruefbar: Neuerzeugung {type(e).__name__}: {str(e)[:120]}"
    finally:
        shutil.rmtree(sand, ignore_errors=True)
    a = {k: v for k, v in live.items() if k != ZEITSTEMPELFELD}
    b = {k: v for k, v in neu.items() if k != ZEITSTEMPELFELD}
    if _kanonisch(a) == _kanonisch(b):
        return None
    teile = []
    felder = sorted(k for k in set(a) | set(b) if k != "rows" and a.get(k) != b.get(k))
    if felder:
        teile.append("Felder " + ", ".join(felder))
    za = {(r.get("did") or r.get("bucket")): r for r in a.get("rows") or []}
    zb = {(r.get("did") or r.get("bucket")): r for r in b.get("rows") or []}
    anders = sum(1 for k in set(za) & set(zb) if za[k] != zb[k])
    for zahl, was in ((len(set(za) - set(zb)), "nur geliefert"),
                      (len(set(zb) - set(za)), "nur in der Neuerzeugung"),
                      (anders, "abweichend")):
        if zahl:
            teile.append(f"{zahl} Zeilen {was}")
    return ("weicht von der Neuerzeugung ab (" + "; ".join(teile or ["Reihenfolge"])
            + f"; geliefert sha256 {hashlib.sha256(_kanonisch(a)).hexdigest()[:12]}…, "
            f"neu {hashlib.sha256(_kanonisch(b)).hexdigest()[:12]}…)")


# Je erzeugte Datei eine Pruefung. Eine erzeugte Datei ohne Eintrag hier wird
# gemeldet, nicht uebergangen; tests/test_webroot_write_watch.py haelt die
# Tabelle gegen die Klasse "generated" in config/webroot_baseline.json.
PRUEFER = {
    "blog/index.html": pruefe_blog_index,
    "registry-proof.json": pruefe_registry_proof,
}


def writes_since(root: str, cursor: float) -> list[tuple[str, float]]:
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(d, name)
            rel = os.path.relpath(full, root)
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
         now: float | None = None, pruefer: dict | None = None) -> tuple[list[str], dict]:
    """Gibt (Meldezeilen, neuer Zustand) zurueck. Leere Liste = still."""
    now = time.time() if now is None else now
    try:
        state = json.load(open(state_path, encoding="utf-8"))
        cursor = float(state["cursor"])
    except FileNotFoundError:
        return [], {"cursor": now, "init": True}
    baseline = json.load(open(baseline_path, encoding="utf-8"))
    erzeugt = set(baseline.get("generated") or {})
    pruefer = PRUEFER if pruefer is None else pruefer
    ledger = load_ledger(ledger_path)
    fremd = []
    for rel, ct in writes_since(root, cursor):
        full = os.path.join(root, rel)
        if von_deploy(rel, ct, _sha256(full), ledger):
            continue
        grund = ""
        if rel in erzeugt or rel in pruefer:
            pruefung = pruefer.get(rel)
            befund = pruefung(full) if pruefung else "erzeugte Datei ohne Pruefung"
            if befund is None:
                continue
            grund = f", {befund}"
        when = dt.datetime.fromtimestamp(ct, dt.timezone.utc)
        size = os.lstat(full).st_size if os.path.lexists(full) else -1
        fremd.append(f"  {rel} — {when:%d.%m.%Y %H:%M:%S}Z, {size} B, "
                     f"{_owner(full)}{grund}")
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
    # Fuer die Probe je erzeugte Datei: dieselbe Pruefung wie im Lauf, ohne
    # auf den naechsten Schreibvorgang zu warten, ohne Zustand und ohne Versand.
    ap.add_argument("--pruefe", metavar="PFAD",
                    help="nur die Pruefung einer erzeugten Datei fahren, relativ zum Root")
    a = ap.parse_args(argv)
    if a.pruefe:
        pruefung = PRUEFER.get(a.pruefe)
        if not pruefung:
            print(f"{a.pruefe}: keine Pruefung in PRUEFER")
            return 2
        befund = pruefung(os.path.join(a.root, a.pruefe))
        print(f"{a.pruefe}: {'Ausgabe des Generators, still' if befund is None else befund}")
        return 0 if befund is None else 1
    lauf = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        alt = json.load(open(a.state, encoding="utf-8"))
    except (OSError, ValueError):
        alt = {}
    lines, state = scan(a.root, a.ledger, a.state, a.baseline)
    if state.get("init"):
        state.pop("init")
        _save_state(a.state, {**state, "zuletzt": dt.datetime.fromtimestamp(
            state["cursor"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "gemeldet": 0, "lauf": lauf})
        print("webroot-write-watch: erster Lauf, Stand gesetzt, nichts gemeldet")
        return 0
    if lines:
        from app import notify
        text = "\n".join(lines)
        print(text)
        # Gedrosselt heisst: derselbe Befund ging frueher schon raus (ops
        # NOTIFY-1 Kriterium 9). Der Stand rueckt vor; sonst meldete die Wache
        # jeden Lauf mit 1, weil die Drosselung ihn nie wieder zustellt.
        zugestellt = notify.send_telegram(text, channel=a.channel)
        gedrosselt = not zugestellt and notify.letzter_grund() == "gedrosselt"
        if not zugestellt and not gedrosselt:
            # Der Meldestand bleibt stehen: der naechste Lauf findet dieselben
            # Schreibvorgaenge und versucht es noch einmal. Das Lebenszeichen
            # wird trotzdem geschrieben — die Wache ist gelaufen, und
            # c-webroot-write-watch-runs fragt nach dem Lauf, nicht nach der
            # Zustellung (ops WRW-2).
            print("webroot-write-watch: Meldung nicht zugestellt, Stand unveraendert",
                  file=sys.stderr)
            _save_state(a.state, {**alt, "lauf": lauf})
            return 1
        if gedrosselt:
            print("webroot-write-watch: Meldung gedrosselt, kein Zustellfehler")
    else:
        print(f"webroot-write-watch: still seit {state['zuletzt']}")
    _save_state(a.state, {**state, "lauf": lauf})
    return 0


if __name__ == "__main__":
    sys.exit(main())
