#!/usr/bin/env python3
"""Schreibschloss fuer getrackte Dateien im Server-Checkout.

Zwei Sitzungen, eine Datei, ein blockierter Deploy — am 07.10.2026 lagen
143 nicht committete Zeilen in `scripts/selftest.py`, waehrend ein PR auf
dieselbe Datei gemergt wurde. `deploy.sh` lehnte ab und der Server blieb drei
Commits zurueck. Am selben Tag ueberschrieb eine Sitzung `~/gate-proof-key.txt`
und liess `did:moltrust:373c7752846d439c` ohne Schluessel zurueck.

Das Schloss verhindert nichts. Es macht sichtbar, wer haelt, seit wann und
wofuer — und das ist der Unterschied zwischen zwei Sitzungen, die sich
abstimmen koennen, und zweien, die es nicht merken.

    from scripts.writelock import acquire, LockHeld

    with acquire("scripts/selftest.py", auftrag="Drossel um offene Befunde"):
        ...

Ein fremdes Schloss, das juenger als vier Stunden ist, wirft `LockHeld`. Aelter
gilt als verwaist und wird uebernommen; die Uebernahme steht im neuen Schloss,
damit sie nicht wie ein Erstzugriff aussieht.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import subprocess

# Der Checkout, gegen den ein Pfad zu seinem Slug wird.
CHECKOUT = "/home/moltstack/moltstack"

# Fest, nicht aus ~ abgeleitet. Auf diesem Host ist ~/moltstack der
# Checkout, und ein Schloss gegen das Schreiben im Checkout hat dort
# nichts zu suchen: es erscheint als `?? .locks/` im Status und steht
# damit in genau der Liste, die der Deploy liest. Zuerst lag es dort.
LOCK_DIR = "/home/moltstack/.moltstack-locks"
STALE_AFTER = dt.timedelta(hours=4)
UTC = dt.timezone.utc


class LockError(RuntimeError):
    """Das Schloss laesst sich nicht bestimmen. Kein Rueckfallwert."""


SLUG_FORM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def session_id() -> tuple[str, str]:
    """(kennung, woher). Die Sitzung, nicht der Prozess.

    Vorher war es `hostname:PID`, und damit konnte keine Sitzung ihr eigenes
    Schloss freigeben: jeder Befehl ist ein eigener Prozess mit eigener PID.
    Am 08.10.2026 blieben drei Schloesser liegen, bis sie nach vier Stunden
    verwaisten — `release()` verweigerte dreimal, obwohl es dieselbe Sitzung
    war.

    `os.getsid(0)` traegt ueber alle Befehle einer Terminalsitzung. Eine
    ausdruecklich gesetzte Kennung geht vor, damit zwei Sitzungen auf derselben
    SID sich trennen koennen.
    """
    for var in ("MOLTRUST_SESSION", "CLAUDE_SESSION_ID"):
        v = os.environ.get(var, "").strip()
        if v:
            return v, var
    try:
        return f"sid:{os.getsid(0)}", "os.getsid"
    except OSError as e:  # pragma: no cover - POSIX hat getsid
        raise LockError(f"Sitzungskennung nicht bestimmbar: {e}") from e


def _git(args: list, cwd: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                         text=True, timeout=20)
    if out.returncode != 0:
        raise LockError(f"git {' '.join(args)} in {cwd}: rc={out.returncode} "
                        f"{out.stderr.strip()[:120]}")
    return out.stdout.strip()


def slug(path: str) -> str:
    """Ein Name je Datei, gleich aus jedem Worktree derselben Repo.

    Vorher war der Slug gegen einen festen Checkout-Pfad gerechnet und hing
    damit am Arbeitsverzeichnis: dieselbe Datei ergab aus dem Checkout
    `scripts-task_watch.py.lock` und aus einem Worktree
    `..-moltstack-wt-wache-scripts-task_watch.py.lock`. Zwei Sitzungen in zwei
    Worktrees kollidierten nie — das Schloss schuetzte nichts.

    `--git-common-dir` ist bei allen Worktrees derselben Repo dasselbe
    Verzeichnis, `--show-toplevel` die Wurzel des jeweiligen Worktrees. Repo
    plus relativer Pfad ergeben denselben Namen, egal von wo.
    """
    ab = os.path.abspath(path)
    cwd = os.path.dirname(ab) if os.path.dirname(ab) else os.getcwd()
    while cwd and not os.path.isdir(cwd):
        cwd = os.path.dirname(cwd)
    common = os.path.realpath(os.path.join(cwd, _git(["rev-parse", "--git-common-dir"], cwd)))
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    rel = os.path.relpath(ab, top)
    if rel.startswith(".."):
        raise LockError(f"{path} liegt nicht unter {top}")
    repo = re.sub(r"[^A-Za-z0-9]", "-", os.path.basename(os.path.dirname(common))
                  or os.path.basename(common)).strip("-")
    name = f"{repo}--{re.sub(r'[^A-Za-z0-9._-]', '-', rel)}.lock"
    if not SLUG_FORM.match(name) or ".." in name:
        # Abweisen, nicht umschreiben. Ein Slug mit fuehrendem Punkt wurde am
        # 08.10. zu einer versteckten Datei, die `ls` und `rm *.lock` nicht
        # sahen — das Schloss lag da und niemand fand es.
        raise LockError(f"Slug {name!r} entspricht nicht {SLUG_FORM.pattern}")
    return name


class LockHeld(RuntimeError):
    """Eine fremde Sitzung haelt die Datei und ist nicht verwaist."""


def _read(p: str) -> dict | None:
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _age(entry: dict, now: dt.datetime) -> dt.timedelta | None:
    try:
        return now - dt.datetime.fromisoformat(str(entry["zeitstempel"]))
    except (KeyError, ValueError, TypeError):
        return None


def inspect(path: str, now: dt.datetime | None = None) -> dict | None:
    """Was auf dieser Datei liegt. None heisst: nichts."""
    now = now or dt.datetime.now(UTC)
    p = os.path.join(LOCK_DIR, slug(path))
    entry = _read(p)
    if entry is None:
        return None
    age = _age(entry, now)
    entry["_alter"] = str(age) if age else "unbekannt"
    entry["_verwaist"] = bool(age and age > STALE_AFTER)
    entry["_eigen"] = entry.get("sitzung") == session_id()[0]
    return entry


def acquire(path: str, auftrag: str, now: dt.datetime | None = None):
    """Nimmt das Schloss oder wirft. Als Kontextmanager benutzbar."""
    now = now or dt.datetime.now(UTC)
    held = inspect(path, now)
    uebernommen = None
    if held and not held["_eigen"]:
        if not held["_verwaist"]:
            raise LockHeld(
                f"{path} haelt {held.get('sitzung', '?')} seit "
                f"{held.get('zeitstempel', '?')} ({held['_alter']}), "
                f"PID {held.get('pid', '?')}, Auftrag: "
                f"{held.get('auftrag', '—')}. Nicht anfangen, melden.")
        uebernommen = {"sitzung": held.get("sitzung"),
                       "zeitstempel": held.get("zeitstempel"),
                       "auftrag": held.get("auftrag"),
                       "alter_bei_uebernahme": held["_alter"]}

    os.makedirs(LOCK_DIR, mode=0o700, exist_ok=True)
    # exist_ok laesst den Modus eines vorhandenen Verzeichnisses stehen,
    # also wird er jedes Mal gesetzt. Ein Schloss sagt, wer woran
    # arbeitet; das geht keinen zweiten Benutzer auf dem Host an.
    os.chmod(LOCK_DIR, 0o700)
    kennung, woher = session_id()
    entry = {"pfad": path, "sitzung": kennung, "sitzung_aus": woher,
             "pid": os.getpid(),
             "zeitstempel": now.isoformat(timespec="seconds"),
             "auftrag": auftrag.strip().splitlines()[0][:200] if auftrag.strip() else "—"}
    if uebernommen:
        entry["uebernommen_von"] = uebernommen
    p = os.path.join(LOCK_DIR, slug(path))
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(entry, fh, indent=1, ensure_ascii=False)
    os.chmod(p, 0o600)

    @contextlib.contextmanager
    def _held():
        try:
            yield entry
        finally:
            release(path)
    return _held()


def release(path: str) -> bool:
    """Gibt das eigene Schloss frei. Ein fremdes bleibt liegen."""
    p = os.path.join(LOCK_DIR, slug(path))
    entry = _read(p)
    if entry is None:
        return False
    if entry.get("sitzung") != session_id()[0]:
        return False
    try:
        os.remove(p)
        return True
    except OSError:
        return False


def listing(now: dt.datetime | None = None) -> list[dict]:
    """Alles, was gerade liegt. Fuer einen Bericht, nicht fuer eine Entscheidung."""
    now = now or dt.datetime.now(UTC)
    out = []
    if not os.path.isdir(LOCK_DIR):
        return out
    for fn in sorted(os.listdir(LOCK_DIR)):
        if not fn.endswith(".lock"):
            continue
        entry = _read(os.path.join(LOCK_DIR, fn))
        if entry is None:
            out.append({"datei": fn, "fehler": "nicht lesbar"})
            continue
        age = _age(entry, now)
        entry["_alter"] = str(age) if age else "unbekannt"
        entry["_verwaist"] = bool(age and age > STALE_AFTER)
        out.append(entry)
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for e in listing():
            mark = "verwaist" if e.get("_verwaist") else "frisch"
            print(f"{e.get('pfad', e.get('datei'))}  {mark}  "
                  f"{e.get('sitzung', '?')}  seit {e.get('zeitstempel', '?')}  "
                  f"{e.get('auftrag', '—')}")
    else:
        print(__doc__)
