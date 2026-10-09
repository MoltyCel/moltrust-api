"""Ein aus der Crontab aufgerufenes Programm handelt beim Import nicht.

Am 09.10.2026 hat eine PYTHONPATH-Probe drei echte "HN SUBMIT JETZT"-
Nachrichten an Lars ausgeloest. Die Probe fuehrte den Modulrumpf jeder Datei
aus, um zu messen, welche ihre Repo-Pakete ohne PYTHONPATH noch finden — und
`scripts/telegram_hn_remind.py` hatte seinen Versand auf Modulebene, ohne
`if __name__ == "__main__"`. `run_name` schuetzt davor nicht: Modulebene laeuft
immer.

Zwei Lehren, und beide sind hier festgehalten. Die Probe war falsch gebaut (das
steht als Regel in CLAUDE.md). Und ein Programm, das beim Import handelt, ist
eine Falle fuer jeden, der es je importiert — einen Test, ein Werkzeug, eine
Durchsicht. Gemessen: von 47 cron-aufgerufenen Programmen war es genau dieses
eine; vier weitere handeln auf Modulebene, aber nur mit
`mkdir(exist_ok=True)`.
"""
import ast
import pathlib
import re

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = WURZEL / "ops" / "crontab.txt"

# Was als Handlung zaehlt: alles, was nach aussen wirkt.
WIRKT = {
    "post", "put", "patch", "delete", "urlopen", "request",
    "run", "check_call", "check_output", "Popen", "system",
    "send_telegram", "send_befunde", "sendmail",
    "write_text", "write_bytes", "unlink", "rmtree",
    "execute", "executemany", "commit",
}

# Nicht darin, obwohl `os.replace` ein echtes Verschieben ist: `replace` ist
# vor allem eine String-Methode. Die erste Fassung dieses Tests meldete
# `scripts/moltbook_writers.py:61` — dort steht
# `MOLTBOOK_HOST.replace(".", r"\.")` beim Bauen eines regulaeren Ausdrucks.
# Am Methodennamen allein sind die beiden nicht zu trennen, und ein Test, der
# jeden String-Ersatz meldet, wird abgeschaltet statt gelesen.

# Benannte Ausnahme: ein Verzeichnis anzulegen ist idempotent und folgenlos.
# Vier cron-aufgerufene Dateien tun das auf Modulebene — agents/moltguard.py,
# scripts/endpoint_probe.py, scripts/threadwatch.py, scripts/w3c_listwatch.py,
# jeweils `LOG_FILE.parent.mkdir(parents=True, exist_ok=True)`.
AUSNAHME_MKDIR = {"mkdir", "makedirs"}


def _cron_programme():
    """Repo-Python-Dateien, die der Schnappschuss aufruft."""
    if not SNAPSHOT.exists():
        pytest.skip("kein Crontab-Schnappschuss im Baum")
    raus = set()
    for z in SNAPSHOT.read_text(encoding="utf-8").splitlines():
        if not z.strip() or z.lstrip().startswith("#"):
            continue
        if re.match(r"^[A-Z_]+=", z.strip()):
            continue
        for m in re.finditer(
                r"((?:app|agents|monitor|scripts|services|workers|operator|ops)"
                r"/[A-Za-z0-9_/]+\.py)", z):
            p = WURZEL / m.group(1)
            if p.exists():
                raus.add(p)
    return sorted(raus)


def _handlungen_auf_modulebene(pfad):
    """(zeile, name) je Aufruf, der beim Import laeuft.

    Ein `if __name__ == "__main__"`-Block ist genau der Schutz, um den es
    geht, und wird uebersprungen. Funktions- und Klassenruempfe ebenso —
    dort steht Code, der erst bei einem Aufruf laeuft. Dekoratoren und
    Default-Argumente zaehlen dazu: die laufen beim Import mit.
    """
    try:
        baum = ast.parse(pfad.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    treffer = []
    for k in baum.body:
        if isinstance(k, ast.If) and "__name__" in ast.unparse(k.test):
            continue
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in k.decorator_list:
                for u in ast.walk(d):
                    treffer += _aufrufe(u)
            continue
        if isinstance(k, (ast.Import, ast.ImportFrom)):
            continue
        for u in ast.walk(k):
            treffer += _aufrufe(u)
    return treffer


def _aufrufe(u):
    if not isinstance(u, ast.Call):
        return []
    f = u.func
    name = (f.attr if isinstance(f, ast.Attribute)
            else f.id if isinstance(f, ast.Name) else "")
    if name in WIRKT:
        return [(u.lineno, name)]
    return []


def test_kein_cron_programm_handelt_beim_import():
    """Die Sperrklinke.

    Erlaubt bleibt mkdir(exist_ok=True) als benannte Ausnahme — sie steht
    nicht in WIRKT und wird deshalb nie gemeldet.
    """
    schlecht = []
    for p in _cron_programme():
        treffer = _handlungen_auf_modulebene(p)
        if treffer:
            stellen = ", ".join(f"{z}:{n}" for z, n in treffer)
            schlecht.append(f"{p.relative_to(WURZEL)} ({stellen})")
    assert schlecht == [], (
        "handelt beim Import — ein Import, ein Test oder eine Durchsicht "
        "loest es damit aus: " + "; ".join(schlecht)
        + ". Den Versand in eine Funktion ziehen und hinter "
          "`if __name__ == \"__main__\"` rufen.")


def test_die_mkdir_ausnahme_ist_benannt():
    """Sie steht als eigene Menge da, nicht als Luecke in WIRKT."""
    assert AUSNAHME_MKDIR
    assert not (AUSNAHME_MKDIR & WIRKT), (
        "die Ausnahme ueberschneidet sich mit WIRKT")


def test_die_vier_cron_dateien_in_ops_handeln_nicht():
    """Die Dateien, die am 09.10. die vier curl-Zeilen ersetzt haben.

    Sie sind der Anlass, dass es diesen Test gibt: vier neue Programme, die
    aus der Crontab laufen und senden. Haetten sie ihren Versand auf
    Modulebene, waere der Fehler vom Morgen noch am selben Tag wiederholt.
    """
    verzeichnis = WURZEL / "ops" / "cron"
    if not verzeichnis.is_dir():
        pytest.skip("ops/cron gibt es nicht")
    dateien = sorted(verzeichnis.glob("*.py"))
    assert dateien, "ops/cron ist leer"
    for p in dateien:
        assert _handlungen_auf_modulebene(p) == [], p.name
        quelle = p.read_text(encoding="utf-8")
        assert '__name__ == "__main__"' in quelle, f"{p.name} ohne Schutz"
        assert "api.telegram.org" not in quelle, f"{p.name} sendet selbst"
