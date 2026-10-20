"""`fremde_programme()` ist eine Zusage an eine andere Sitzung.

Sie haengt die Liste an die 08:00-Meldung und ruft `scan_file` je Datei. Was
hier zugesagt wird: absolute Pfade, nur `.py`, nur ausserhalb des Repos, nur
Dateien die es gibt, ohne Doppelte — und ein Import, der nichts ausloest.

Der letzte Punkt ist der wichtigste und war vorher verletzt: `GETRACKT =
getrackt()` stand auf Modulebene und fuehrte beim Import `git ls-files` aus.
`scripts/module_effects.py` haette das nicht gemeldet, weil ein Aufruf einer
lokalen Funktion fuer ihn `other` ist und nicht `process` — die Blindstelle
ist in der Antwort an die Nebensitzung benannt.

Dazu die Zugehoerigkeitsfrage selbst: „im Repo" wird nicht als Pfadvergleich
entschieden, sondern daran, ob der Checkout der Datei unser git-common-dir
teilt und sie dort getrackt ist. Zwei Pfadvergleiche waren vorher falsch — ein
fester Pfad hielt aus einem Arbeitsbaum dessen eigene Dateien fuer fremd, und
ein aus `__file__` abgeleiteter hielt aus dem Arbeitsbaum die 50 Programme des
ausgerollten Checkouts fuer fremd.
"""
import pathlib
import subprocess
import types

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
QUELLE = WURZEL / "scripts" / "crontab_inventar.py"


def _modul():
    """Die Quelle lesen und compilieren — kein Bytecode-Cache dazwischen."""
    m = types.ModuleType("ci_test")
    m.__file__ = str(QUELLE)
    m.__name__ = "ci_test"
    exec(compile(QUELLE.read_text(encoding="utf-8"), str(QUELLE), "exec"),
         m.__dict__)
    return m


def test_der_import_loest_nichts_aus(monkeypatch):
    """Kein Unterprozess, kein Netzaufruf, kein Schreiben beim Import."""
    gerufen = []
    echt = subprocess.run
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: (gerufen.append(a[0] if a else k), echt(*a, **k))[1])
    m = _modul()
    assert gerufen == [], f"Unterprozess beim Import: {gerufen}"
    assert m._UNSER is None, "unser_common_dir() lief beim Import"


def test_gibt_absolute_py_pfade_ausserhalb_des_repos():
    m = _modul()
    raus = m.fremde_programme()
    assert raus, "keine fremden Programme gefunden"
    for p in raus:
        assert p.startswith("/"), f"nicht absolut: {p}"
        assert p.endswith(".py"), f"keine .py-Datei: {p}"
        assert pathlib.Path(p).is_file(), f"existiert nicht: {p}"
        assert not p.startswith(str(WURZEL) + "/"), f"liegt im Repo: {p}"
    assert len(raus) == len(set(raus)), "Doppelte in der Liste"
    assert raus == sorted(raus), "nicht sortiert"


def test_nimmt_einen_schnappschuss(tmp_path):
    """Damit die Pruefung nicht von der Crontab des Rechners abhaengt."""
    m = _modul()
    schnipsel = tmp_path / "crontab.txt"
    schnipsel.write_text(
        "# ein Kommentar\n"
        "SHELL=/bin/bash\n"
        f"0 8 * * * cd {WURZEL} && python3 scripts/selftest.py\n"
        f"0 9 * * * python3 {QUELLE}\n"
        "0 10 * * * python3 /gibt/es/nicht.py\n"
        "0 11 * * * /bin/true\n", encoding="utf-8")
    raus = m.fremde_programme(str(schnipsel))
    # selftest.py liegt im Repo, crontab_inventar.py auch, /gibt/es/nicht.py
    # existiert nicht, /bin/true ist keine .py — also nichts.
    assert raus == [], raus


def test_findet_eine_datei_ausserhalb_ueber_das_cd(tmp_path):
    """Ohne das `cd` der Zeile faende man einen relativen Pfad nicht.

    Genau daran ist die erste Bestandsaufnahme am 09.10.2026 gescheitert:
    `cd ~/moltycelbot && python3 scripts/discovery.py` wurde als "nicht
    aufloesbar" gemeldet, und damit fehlte das eine Programm ausserhalb des
    Repos, das selbst an Telegram sendet.
    """
    m = _modul()
    fremd = tmp_path / "fremd"
    (fremd / "scripts").mkdir(parents=True)
    ziel = fremd / "scripts" / "etwas.py"
    ziel.write_text("x = 1\n", encoding="utf-8")
    schnipsel = tmp_path / "crontab.txt"
    schnipsel.write_text(
        f"0 8 * * * cd {fremd} && python3 scripts/etwas.py\n", encoding="utf-8")
    assert m.fremde_programme(str(schnipsel)) == [str(ziel)]


def test_zaehlt_eine_datei_auf_zwei_zeilen_einmal(tmp_path):
    """~/daily_report.py steht auf zwei Crontab-Zeilen."""
    m = _modul()
    fremd = tmp_path / "f"
    fremd.mkdir()
    ziel = fremd / "doppelt.py"
    ziel.write_text("x = 1\n", encoding="utf-8")
    schnipsel = tmp_path / "crontab.txt"
    schnipsel.write_text(
        f"0 8 * * * python3 {ziel}\n0 9 * * * python3 {ziel}\n",
        encoding="utf-8")
    assert m.fremde_programme(str(schnipsel)) == [str(ziel)]


def test_die_drei_bekannten_sind_dabei():
    """Stand 10.10.2026 — die Liste darf sich aendern, aber nicht lautlos."""
    m = _modul()
    raus = set(m.fremde_programme())
    erwartet = {
        "/home/moltstack/daily_report.py",
        "/home/moltstack/moltrust-knowledge/weekly_summary.py",
        "/home/moltstack/moltycelbot/scripts/discovery.py",
    }
    if not erwartet <= raus:
        pytest.skip(f"andere Maschine oder Crontab: {sorted(raus)}")
    assert erwartet <= raus


def test_zugehoerigkeit_gilt_fuer_jeden_checkout():
    """`im_repo()` sagt von jedem Ort dasselbe.

    Dieselbe Datei in zwei Checkouts dieses Repositorys — der ausgerollte und
    ein Arbeitsbaum — gehoert in beiden dazu. Ein fremdes Repositorium nicht,
    auch wenn die Datei dort getrackt ist: /home/moltstack ist selbst eines,
    und ~/daily_report.py waere ohne die common-dir-Pruefung "im Repo".
    """
    m = _modul()
    eigen = WURZEL / "scripts" / "crontab_inventar.py"
    assert m.im_repo(eigen) is True, eigen

    # Der ausgerollte Checkout, falls er hier liegt.
    deploy = pathlib.Path("/home/moltstack/moltstack/scripts/selftest.py")
    if deploy.is_file():
        assert m.im_repo(deploy) is True, deploy

    fremd = pathlib.Path("/home/moltstack/daily_report.py")
    if fremd.is_file():
        assert m.im_repo(fremd) is False, fremd


def test_eine_ungetrackte_datei_im_baum_gilt_nicht_als_im_repo(tmp_path):
    """Sie waere beim naechsten frischen Checkout weg."""
    m = _modul()
    neu = WURZEL / "scripts" / "_nur_fuer_den_test.py"
    neu.write_text("x = 1\n", encoding="utf-8")
    try:
        assert m.im_repo(neu) is False
    finally:
        neu.unlink()
