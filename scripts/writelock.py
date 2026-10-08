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
import socket

# Der Checkout, gegen den ein Pfad zu seinem Slug wird.
CHECKOUT = "/home/moltstack/moltstack"

# Fest, nicht aus ~ abgeleitet. Auf diesem Host ist ~/moltstack der
# Checkout, und ein Schloss gegen das Schreiben im Checkout hat dort
# nichts zu suchen: es erscheint als `?? .locks/` im Status und steht
# damit in genau der Liste, die der Deploy liest. Zuerst lag es dort.
LOCK_DIR = "/home/moltstack/.moltstack-locks"
STALE_AFTER = dt.timedelta(hours=4)
UTC = dt.timezone.utc


class LockHeld(RuntimeError):
    """Eine fremde Sitzung haelt die Datei und ist nicht verwaist."""


def session_id() -> str:
    """Wer wir sind. Erst die gesetzte Kennung, dann Host und PID."""
    for var in ("MOLTRUST_SESSION", "CLAUDE_SESSION_ID", "TMUX_PANE"):
        v = os.environ.get(var, "").strip()
        if v:
            return v
    return f"{socket.gethostname()}:{os.getpid()}"


def slug(path: str) -> str:
    """Ein Dateiname je Pfad. Kein Verzeichnisbaum unter .locks, damit ein
    Schloss nicht zwischen zwei Ebenen verschwindet."""
    rel = os.path.relpath(os.path.abspath(path), CHECKOUT)
    return re.sub(r"[^A-Za-z0-9_.-]", "-", rel).strip("-") + ".lock"


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
    entry["_eigen"] = entry.get("sitzung") == session_id()
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
    entry = {"pfad": path, "sitzung": session_id(), "pid": os.getpid(),
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
    if entry.get("sitzung") != session_id():
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
