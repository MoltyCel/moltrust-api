"""Die Namenswache übersteht einen Force-Push.

`GUARD_RANGE` ist beim Push `github.event.before..github.sha`. Nach einem
Force-Push ist `before` unerreichbar — `actions/checkout` holt einen
verworfenen Commit nicht —, `git rev-list before..after` endet mit 128, und die
Wache stürzte mit einer Traceback ab, statt irgendetwas zu prüfen. Sie stürzte
an einem gewöhnlichen `--amend`. Eine Pflichtprüfung, die aus einem
Werkzeugfehler rot wird, ist die Lage, die zum Admin-Bypass verführt;
moltguard#58 lief am 06.10.2026 genau dorthin.

Die Tests bauen ein kleines Depot und prüfen vier Fälle. Zwei davon haben in
der ersten Fassung des Fixes versagt: ein leerer Rückfallbereich übersprang die
Prüfung und meldete grün, und `git rev-parse` gibt jede formgültige 40-Hex-SHA
zurück, auch eine, die es nicht gibt.

Die reservierte Kennung wird aus Teilen gebaut, wie in der Wache selbst — sonst
enthielte diese Datei sie und die Wache meldete sich selbst.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "reserved_names_guard.py"
FAKE = "0123456789abcdef0123456789abcdef01234567"      # formgültig, existiert nicht
GONE = "fedcba9876543210fedcba9876543210fedcba98"      # dito
RESERVED = "a" + "ae" + "-" + "0" + "4"


def _run(*args, cwd, env=None):
    e = dict(os.environ)
    e.pop("GUARD_RANGE", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(GUARD), *args], cwd=cwd, env=e,
                          capture_output=True, text=True)


@pytest.fixture()
def depot(tmp_path):
    """Ein Depot mit drei Commits; der dritte nennt die reservierte Kennung."""
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True,
                                    capture_output=True, text=True)
    git("init", "-q", "-b", "main", ".")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "T")
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    shutil.copy(GUARD, tmp_path / ".github" / "scripts" / GUARD.name)
    (tmp_path / ".github" / "reserved-names-baseline").write_text("")
    (tmp_path / "datei.txt").write_text("eins\n")
    git("add", "-A")
    git("commit", "-q", "-m", "erster Commit, harmlos")
    base = git("rev-parse", "HEAD").stdout.strip()
    (tmp_path / "datei.txt").write_text("eins\nzwei\n")
    git("commit", "-qam", "zweiter Commit, harmlos")
    harmlos = git("rev-parse", "HEAD").stdout.strip()
    (tmp_path / "datei.txt").write_text("eins\nzwei\ndrei\n")
    git("commit", "-qam", f"dritter Commit nennt {RESERVED} in der Nachricht")
    reserviert = git("rev-parse", "HEAD").stdout.strip()
    return tmp_path, base, harmlos, reserviert


def test_normaler_bereich_geht_durch(depot):
    path, base, harmlos, _ = depot
    r = _run("ci", cwd=path, env={"GUARD_RANGE": f"{base}..{harmlos}"})
    assert r.returncode == 0, r.stderr


def test_force_push_mit_harmlosem_kopf_geht_durch(depot):
    """Der Fall, der vorher eine Traceback warf."""
    path, _, harmlos, _ = depot
    r = _run("ci", cwd=path, env={"GUARD_RANGE": f"{FAKE}..{harmlos}"})
    assert r.returncode == 0, r.stderr
    assert "does not resolve" in r.stderr, "der Rückfall soll gesagt werden, nicht stillschweigen"
    assert "Traceback" not in r.stderr


def test_force_push_findet_die_kennung_trotzdem(depot):
    """Die Lücke der ersten Fassung: leerer Rückfallbereich meldete grün.

    Auf einem Push in den Standardzweig ist `main..head` leer, weil der Kopf
    die Spitze *ist*. Wer dann nichts prüft, meldet grün über eine Nachricht,
    die er nie gelesen hat.
    """
    path, _, _, reserviert = depot
    r = _run("ci", cwd=path, env={"GUARD_RANGE": f"{FAKE}..{reserviert}"})
    assert r.returncode == 1, f"die Kennung wurde übersehen: {r.stderr}"
    assert "reserved identifier in the message" in r.stderr


def test_kopf_existiert_nicht_ist_rot_nicht_gruen(depot):
    """Keine Prüfung, kein Grün — und keine Traceback.

    `git rev-parse` gibt jede formgültige SHA zurück, auch eine, die es nicht
    gibt; die erste Fassung lief damit in ein `git log` auf einen Phantom-
    Commit und stürzte ab.
    """
    path, _, _, _ = depot
    r = _run("ci", cwd=path, env={"GUARD_RANGE": f"{FAKE}..{GONE}"})
    assert r.returncode == 1, r.stderr
    assert "UNREADABLE" in r.stderr
    assert "Traceback" not in r.stderr


def test_laeuft_auf_der_system_python(depot):
    """Die Wache ist auch ein Pre-Commit-Hook und läuft mit dem, was da ist.

    `list | None` als Annotation ist auf 3.9 ein TypeError beim Import — nicht
    erst im Aufruf. Der Hook wäre für jeden Commit auf dem Mac kaputt gewesen.
    """
    path, _, _, _ = depot
    r = subprocess.run([sys.executable, "-c",
                        f"import ast,sys; ast.parse(open({str(GUARD)!r}).read()); print('ok')"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "from __future__ import annotations" in GUARD.read_text(encoding="utf-8")
