#!/usr/bin/env python3
"""Punkt 4: was laeuft aus der Crontab und liegt nicht im Repo.

Je aktive Zeile: der Pfad des aufgerufenen Programms, ob er im Repo liegt, ob
er selbst an api.telegram.org sendet, und wann er zuletzt geaendert wurde.

Nur lesen.
"""
import ast
import datetime as dt
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path("/home/moltstack/moltstack")
MUSTER = re.compile(r"api\.telegram\.org")
TRANSPORT = {"post", "get", "request", "urlopen", "Request", "send"}


def getrackt():
    """Alle Pfade, die git im Repo kennt. Ein Programm, das im Baum liegt aber
    nicht getrackt ist, zaehlt nicht als 'im Repo' — es waere beim naechsten
    frischen Checkout weg."""
    p = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                       capture_output=True, text=True, check=True)
    return {str(REPO / z) for z in p.stdout.splitlines()}


GETRACKT = getrackt()


def sendet_selbst(pfad: pathlib.Path) -> str:
    """ja / nein / '?' wenn nicht lesbar."""
    try:
        text = pfad.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "?"
    if not MUSTER.search(text):
        return "nein"
    if pfad.suffix == ".py":
        try:
            baum = ast.parse(text)
        except SyntaxError:
            return "?"
        literale = set()
        for k in ast.walk(baum):
            if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                    and MUSTER.search(k.value):
                literale.add(k)
            elif isinstance(k, ast.JoinedStr):
                s = "".join(v.value for v in k.values
                            if isinstance(v, ast.Constant)
                            and isinstance(v.value, str))
                if MUSTER.search(s):
                    literale.add(k)
        if not literale:
            return "nein"      # nur im Kommentar
        for k in ast.walk(baum):
            if not isinstance(k, ast.Call):
                continue
            f = k.func
            n = (f.attr if isinstance(f, ast.Attribute)
                 else f.id if isinstance(f, ast.Name) else "")
            if n in TRANSPORT:
                for arg in list(k.args) + [kw.value for kw in k.keywords]:
                    for u in ast.walk(arg):
                        if u in literale:
                            return "ja"
        return "nein"
    # Shell: curl in derselben oder einer der fuenf Zeilen davor
    zeilen = text.splitlines()
    for i, ln in enumerate(zeilen, 1):
        if MUSTER.search(ln) and not ln.strip().startswith("#"):
            if "curl" in "\n".join(zeilen[max(0, i - 6):i]):
                return "ja"
    return "nein"


# Das aufgerufene Programm aus einer Crontab-Zeile ziehen.
#
# Nicht das erste Wort: die Zeilen fangen mit `set -a && source ... && cd ...
# && <python> <skript>` an. Gesucht ist das Argument hinter einem Interpreter,
# oder, wenn keiner da ist, das erste ausfuehrbare Ding mit einem Pfad.
INTERPRETER = re.compile(r"(?:^|/)(python3?|bash|sh|node|npm|psql)$")


def programm(befehl: str, volle_zeile: str = ""):
    """(pfad_oder_none, rohtext)"""
    wd = _cd_aus(volle_zeile or befehl)
    teile = befehl.split()
    for i, t in enumerate(teile):
        if t == "-m" and i + 1 < len(teile):
            # python -m paket.modul -> das Modul im Repo suchen
            modul = teile[i + 1]
            for basis in ([pathlib.Path(wd)] if wd else []) + [REPO]:
                kand = basis / (modul.replace(".", "/") + ".py")
                if kand.exists():
                    return kand, modul
                kand = basis / modul.replace(".", "/") / "__init__.py"
                if kand.exists():
                    return kand, modul
            return None, modul
    for i, t in enumerate(teile):
        if INTERPRETER.search(t.rstrip(":")):
            for u in teile[i + 1:]:
                if u.startswith("-"):
                    continue
                return _aufloesen(u, wd), u
            return None, t
    for t in teile:
        if "/" in t and not t.startswith("-") and "=" not in t:
            return _aufloesen(t, wd), t
    return None, befehl.split()[0] if befehl.split() else "?"


def _aufloesen(roh: str, arbeitsverzeichnis=None):
    """Einen relativen Pfad auflosen — mit dem `cd` der Zeile.

    Ohne das landet `cd ~/moltycelbot && python3 scripts/discovery.py` als
    "nicht aufloesbar", und genau diese Zeile ist der Fund: ein Programm
    ausserhalb des Repos, das selbst sendet.
    """
    roh = roh.strip("\"'")
    roh = os.path.expanduser(roh)
    p = pathlib.Path(roh)
    if p.is_absolute():
        return p if p.exists() else None
    basen = []
    if arbeitsverzeichnis:
        basen.append(pathlib.Path(arbeitsverzeichnis))
    basen += [REPO, pathlib.Path("/home/moltstack")]
    for basis in basen:
        if (basis / p).exists():
            return basis / p
    return None


def _cd_aus(befehl: str):
    """Das letzte `cd <ziel>` der Zeile — das gilt beim Aufruf."""
    treffer = re.findall(r"\bcd\s+([^\s&;|]+)", befehl)
    if not treffer:
        return None
    return os.path.expanduser(treffer[-1].strip("\"'"))


def main():
    alle = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                          check=True).stdout.splitlines()
    print(f"Crontab: {len(alle)} Zeilen")
    aktiv = [(i + 1, z) for i, z in enumerate(alle)
             if z.strip() and not z.lstrip().startswith("#")
             and not re.match(r"^[A-Z_]+=", z.strip())]
    print(f"davon aktive Job-Zeilen: {len(aktiv)}\n")

    kopf = (f"{'Zl':>4}  {'Programm':<58} {'Repo':<5} {'sendet':<6} "
            f"{'geaendert':<16}")
    print(kopf)
    print("-" * len(kopf))

    ausserhalb = []
    for nr, zeile in aktiv:
        # Zeitplan abtrennen (5 Felder, oder @reboot)
        rest = zeile.strip()
        if rest.startswith("@"):
            rest = rest.split(None, 1)[1] if " " in rest else ""
        else:
            teile = rest.split(None, 5)
            rest = teile[5] if len(teile) > 5 else ""
        pfad, roh = programm(rest, zeile)
        if pfad is None:
            print(f"{nr:>4}  {roh[:58]:<58} {'?':<5} {'?':<6} {'-':<16}")
            ausserhalb.append((nr, roh, None))
            continue
        im_repo = str(pfad) in GETRACKT
        snd = sendet_selbst(pfad)
        mtime = dt.datetime.fromtimestamp(pfad.stat().st_mtime,
                                          dt.timezone.utc)
        kurz = str(pfad)
        if kurz.startswith(str(REPO) + "/"):
            kurz = kurz[len(str(REPO)) + 1:]
        elif kurz.startswith("/home/moltstack/"):
            kurz = "~/" + kurz[len("/home/moltstack/"):]
        print(f"{nr:>4}  {kurz[:58]:<58} {'ja' if im_repo else 'NEIN':<5} "
              f"{snd:<6} {mtime:%Y-%m-%d %H:%M}")
        if not im_repo:
            ausserhalb.append((nr, kurz, snd))

    print()
    print(f"=== ausserhalb des Repos: {len(ausserhalb)} Zeilen ===")
    for nr, kurz, snd in ausserhalb:
        print(f"  Zeile {nr}: {kurz}" + (f"  (sendet selbst: {snd})"
                                         if snd else "  (nicht aufloesbar)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
