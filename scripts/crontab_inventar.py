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

# Aus der eigenen Lage, nicht festgeschrieben: dieses Werkzeug laeuft
# auch aus einem Arbeitsbaum, und dort hielt ein fester Pfad die
# eigenen Repo-Dateien fuer "ausserhalb des Repos".
REPO = pathlib.Path(__file__).resolve().parent.parent
MUSTER = re.compile(r"api\.telegram\.org")
TRANSPORT = {"post", "get", "request", "urlopen", "Request", "send"}


def _common_dir(start) -> str:
    """Das gemeinsame git-Verzeichnis zu `start`, oder "" wenn keines."""
    try:
        r = subprocess.run(["git", "-C", str(start), "rev-parse",
                            "--git-common-dir"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode != 0:
        return ""
    pfad = r.stdout.strip()
    if not pfad:
        return ""
    # `--git-common-dir` kann relativ zum Arbeitsverzeichnis sein.
    return str((pathlib.Path(start) / pfad).resolve()) if not \
        pathlib.Path(pfad).is_absolute() else str(pathlib.Path(pfad).resolve())


def unser_common_dir() -> str:
    global _UNSER
    if _UNSER is None:
        _UNSER = _common_dir(pathlib.Path(__file__).resolve().parent)
    return _UNSER


_UNSER = None


def im_repo(pfad) -> bool:
    """Gehoert diese Datei zu diesem Repositorium, in irgendeinem Checkout.

    Nicht ein Pfadvergleich: der war in beiden vorigen Fassungen die
    Fehlerquelle. Geprueft wird, ob ihr Checkout unser git-common-dir teilt
    und ob sie dort getrackt ist.
    """
    pfad = pathlib.Path(pfad)
    unser = unser_common_dir()
    if not unser:
        return False
    if _common_dir(pfad.parent) != unser:
        return False
    try:
        top = subprocess.run(["git", "-C", str(pfad.parent), "rev-parse",
                              "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
        if top.returncode != 0:
            return False
        rel = pfad.resolve().relative_to(pathlib.Path(top.stdout.strip()).resolve())
        r = subprocess.run(["git", "-C", top.stdout.strip(), "ls-files",
                            "--error-unmatch", str(rel)],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


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


def at_jobs():
    """Die vierte Startstelle: at-Jobs.

    Am 09.10.2026 lief Job 7 aus ~/bin/r4-run.sh — ausserhalb des Repos, von
    keiner Durchsicht erfasst, mit einer seit dem 08.10. zerbrochenen
    printf-Zeile. Weder die Crontab-Bestandsaufnahme noch die
    Invariantenprobe sahen ihn, weil at eine eigene Warteschlange ist.
    """
    try:
        q = subprocess.run(["atq"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    raus = []
    for zeile in (q.stdout or "").splitlines():
        teile = zeile.split()
        if not teile:
            continue
        jobid = teile[0]
        try:
            d = subprocess.run(["at", "-c", jobid], capture_output=True,
                               text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raus.append((jobid, zeile, None))
            continue
        raus.append((jobid, zeile, d.stdout or ""))
    return raus


def bin_verzeichnis():
    """~/bin: liegt ausserhalb des Repos und wird von Hand gepflegt.

    deploy.sh kommt per Selbstinstallation aus ops/deploy/deploy.sh (WORKFLOW
    11.6). Alles andere dort hat keine Quelle im Repo.
    """
    d = pathlib.Path("/home/moltstack/bin")
    if not d.is_dir():
        return []
    raus = []
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        raus.append((f, im_repo(f), sendet_selbst(f),
                     dt.datetime.fromtimestamp(f.stat().st_mtime,
                                               dt.timezone.utc)))
    return raus


def fremde_programme(quelle: str | None = None) -> list:
    """Absolute Pfade der .py-Programme, die aus der Crontab laufen und nicht
    im Repo liegen.

    Fuer `scripts/module_effects.py`: die Dateien, die `--check .` nicht
    sieht, weil sie ausserhalb des Repos liegen. Am 09.10.2026 waren zwei
    davon mit einer gegateten Modulebenen-Wirkung besetzt —
    `~/moltycelbot/scripts/discovery.py` (file write, process) und
    `~/moltrust-knowledge/weekly_summary.py` (file write).

    `quelle` ist ein Crontab-Schnappschuss; ohne Angabe wird die lebende
    Crontab gelesen. Fuer die 08:00-Meldung auf dem Server ist die lebende die
    richtige: sie sagt, was wirklich laeuft.

    Aufgeloest wird samt `cd` der Zeile — ohne das findet man
    `~/moltycelbot/scripts/discovery.py` nicht, weil die Zeile nur
    `scripts/discovery.py` nennt.

    Nur Dateien, die es gibt: ein Pfad, der auf nichts zeigt, ist ein eigener
    Befund (die Crontab rief bis zum 09.10. `/tmp/refresh_tweets_0800.py`,
    das es nicht mehr gab) und gehoert in die Bestandsaufnahme, nicht in eine
    Liste zu scannender Dateien.
    """
    if quelle:
        text = pathlib.Path(quelle).read_text(encoding="utf-8")
    else:
        text = subprocess.run(["crontab", "-l"], capture_output=True,
                              text=True, check=True).stdout

    raus = []
    for zeile in text.splitlines():
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        if re.match(r"^[A-Z_]+=", zeile.strip()):
            continue
        rest = zeile.strip()
        if rest.startswith("@"):
            rest = rest.split(None, 1)[1] if " " in rest else ""
        else:
            teile = rest.split(None, 5)
            rest = teile[5] if len(teile) > 5 else ""
        pfad, _roh = programm(rest, zeile)
        if pfad is None or pfad.suffix != ".py":
            continue
        if im_repo(pfad):
            continue
        if not pfad.is_file():
            continue
        raus.append(str(pfad))
    # Doppelte zusammen: ~/daily_report.py steht auf zwei Zeilen.
    return sorted(set(raus))


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
        im_repo = im_repo(pfad)
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

    # Zeilen, die selbst senden, ohne ein Programm dafuer zu rufen. Die sieht
    # keine Datei-Durchsicht: der Programmtext steht in der Crontab.
    eigene = [nr for nr, z in aktiv if "api.telegram.org" in z]
    print()
    print(f"=== Crontab-Zeilen, die SELBST an Telegram senden: "
          f"{len(eigene)} ===")
    for nr in eigene:
        print(f"  Zeile {nr}: curl in der Zeile, kein Programm im Repo")

    print()
    jobs = at_jobs()
    print(f"=== at-Jobs (vierte Startstelle): {len(jobs)} ===")
    for jobid, zeile, inhalt in jobs:
        pfade = sorted(set(re.findall(r"/[A-Za-z0-9_./~-]+\.(?:sh|py)",
                                      inhalt or "")))
        drin = [p for p in pfade if im_repo(p)]
        print(f"  Job {jobid}: {zeile[:46]}")
        for pf in pfade:
            print(f"      {pf}  Repo={'ja' if im_repo(pf) else 'NEIN'}")
        if not pfade:
            print("      kein Dateipfad im Job-Text")
        _ = drin

    print()
    binz = bin_verzeichnis()
    print(f"=== ~/bin (ausserhalb des Repos): {len(binz)} Dateien ===")
    for f, im_repo, snd, mtime in binz:
        print(f"  {f.name:28} Repo={'ja' if im_repo else 'NEIN':4} "
              f"sendet={snd:5} {mtime:%Y-%m-%d %H:%M}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
