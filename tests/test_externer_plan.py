"""Der externe Zeitplan: vier Laeufe am Tag, Toleranz zwei Stunden.

Bis zum 09.10.2026 stand im Workflow `17 * * * *`, und GitHub lieferte von
einem stuendlichen Plan etwa ein Drittel: Laeufe um 02:09, 21:19, 15:36, 07:24
und 01:00, Stunden auseinander und zu beliebigen Minuten. Die Invariante stand
damit dauerhaft auf WARN, und eine Invariante, die immer gelb ist, lehrt alle,
sie zu uebersehen.

Vier Laeufe am Tag reichen fuer das, was diese Haelfte abdeckt: dass der
Server schweigt. Die Puenktlichkeit liegt seit dem 04.10. bei healthchecks.io,
und der stuendliche Selbsttest laeuft ohnehin lokal.

Die Frist war vorher in Takten gezaehlt — drei ausgelassene. Das sind
stuendlich drei Stunden Stille und bei vier Laeufen am Tag achtzehn, also bei
derselben Zahl zwei voellig verschiedene Wachen. Gerechnet wird jetzt mit der
Toleranz.
"""
import datetime as dt
import importlib.util
import pathlib
import re

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
SUPERVISE = WURZEL / ".github" / "workflows" / "supervise.yml"


def _modul():
    spec = importlib.util.spec_from_file_location(
        "cer", WURZEL / "scripts" / "check_external_runs.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_supervise_laeuft_viermal_am_tag():
    crons = re.findall(r"^\s*-\s*cron:\s*[\"']?([^\"'\n#]+)",
                       SUPERVISE.read_text(encoding="utf-8"), re.M)
    crons = [c.strip() for c in crons]
    assert crons == ["0 */6 * * *"], crons


def test_vier_faellige_takte_am_tag():
    """Aus dem cron gerechnet, nicht aus der Absicht gelesen."""
    m = _modul()
    now = dt.datetime(2026, 10, 9, 9, 0, tzinfo=dt.timezone.utc)
    fires = m.previous_fires("0 */6 * * *", now, count=8)
    # Acht Takte zurueck sind zwei Tage; die Abstaende muessen alle 6 h sein.
    abstaende = {(a - b).total_seconds() for a, b in zip(fires, fires[1:])}
    assert abstaende == {6 * 3600}, abstaende
    assert len(fires) == 8


def test_die_toleranz_ist_zwei_stunden():
    m = _modul()
    assert m.MAX_TOLERANZ == dt.timedelta(hours=2)


def test_das_fenster_ist_nicht_mehr_der_ganze_takt():
    """Mit dem Takt als Fenster waere ein Lauf, der fuenf Stunden fuenfzig
    Minuten zu spaet kommt, puenktlich. Das misst nichts."""
    m = _modul()
    now = dt.datetime(2026, 10, 9, 9, 0, tzinfo=dt.timezone.utc)
    sechs = m.previous_fires("0 */6 * * *", now, count=4)
    assert m._cadence("0 */6 * * *", sechs) == dt.timedelta(hours=2)
    # Beim stuendlichen Plan bleibt es der Takt, weil der kleiner ist.
    stunde = m.previous_fires("17 * * * *", now, count=4)
    assert m._cadence("17 * * * *", stunde) == dt.timedelta(hours=1)


def test_ein_lauf_kurz_nach_dem_takt_ist_puenktlich(monkeypatch):
    """06:00 faellig, 07:07 gelaufen — innerhalb der Toleranz.

    Nach den Messungen vom 09.10. liegt GitHubs Verzug auf diesem Workflow im
    Mittel bei 67 und schlimmstenfalls bei 97 Minuten. Die zwei Stunden sind
    also knapp, aber sie passen — mit rund zwanzig Minuten Luft.
    """
    m = _modul()
    now = dt.datetime(2026, 10, 9, 9, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(m, "token", lambda: "x")
    monkeypatch.setattr(m, "declared", lambda: [("supervise.yml", ["0 */6 * * *"])])
    # Vom echten git-Log geloest: sonst haengt der Test daran, wann
    # der Takt zuletzt geaendert wurde, und das war heute.
    monkeypatch.setattr(m, "takt_geaendert_am",
                        lambda n, s: dt.datetime(
                            2026, 9, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "first_on_default", lambda n: dt.datetime(
        2026, 10, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "newest_scheduled", lambda n: dt.datetime(
        2026, 10, 9, 7, 7, tzinfo=dt.timezone.utc))
    assert m._check(now) == 0


def test_acht_stunden_stille_werden_gemeldet(monkeypatch, capsys):
    """Sechs Stunden Takt plus zwei Stunden Nachsicht. Danach spricht sie.

    Das ist der Fall, fuer den die Wache da ist: der Server schweigt. Mit der
    alten Zaehlung in Takten waeren es achtzehn Stunden gewesen.
    """
    m = _modul()
    now = dt.datetime(2026, 10, 9, 9, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(m, "token", lambda: "x")
    monkeypatch.setattr(m, "declared", lambda: [("supervise.yml", ["0 */6 * * *"])])
    # Vom echten git-Log geloest: sonst haengt der Test daran, wann
    # der Takt zuletzt geaendert wurde, und das war heute.
    monkeypatch.setattr(m, "takt_geaendert_am",
                        lambda n, s: dt.datetime(
                            2026, 9, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "first_on_default", lambda n: dt.datetime(
        2026, 10, 1, tzinfo=dt.timezone.utc))
    # Letzter Lauf 05:00 — der Takt um 06:00 ist um 08:00 ueberfaellig.
    monkeypatch.setattr(m, "newest_scheduled", lambda n: dt.datetime(
        2026, 10, 9, 5, 0, tzinfo=dt.timezone.utc))
    # _check gibt die Zahl der Befunde zurueck, nicht den Prozesscode — am
    # gesunden Fall oben ist das 0, hier 1.
    assert m._check(now) == 1
    aus = capsys.readouterr()
    assert "VERPASST" in aus.err, aus.err
    assert "Toleranz von 2 h" in aus.err, aus.err
    # Die letzte Zeile auf stdout ist der Wert der Invariante.
    assert aus.out.strip().splitlines()[-1] == "1", aus.out


def test_knapp_unter_der_toleranz_schweigt_sie(monkeypatch, capsys):
    """Um 07:30 ist der 06:00-Takt erst 90 Minuten alt — noch keine Meldung."""
    m = _modul()
    now = dt.datetime(2026, 10, 9, 7, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(m, "token", lambda: "x")
    monkeypatch.setattr(m, "declared", lambda: [("supervise.yml", ["0 */6 * * *"])])
    # Vom echten git-Log geloest: sonst haengt der Test daran, wann
    # der Takt zuletzt geaendert wurde, und das war heute.
    monkeypatch.setattr(m, "takt_geaendert_am",
                        lambda n, s: dt.datetime(
                            2026, 9, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "first_on_default", lambda n: dt.datetime(
        2026, 10, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "newest_scheduled", lambda n: dt.datetime(
        2026, 10, 9, 0, 5, tzinfo=dt.timezone.utc))
    m._check(now)
    aus = capsys.readouterr()
    assert "VERPASST" not in aus.err, aus.err
    assert aus.out.strip().splitlines()[-1] == "0", aus.out


# -- das Aufwaermfenster eines GEAENDERTEN Takts -----------------------------

def test_takt_geaendert_am_liest_die_historie():
    """Wann der Takt in Kraft ist, nicht wann die Datei entstand.

    In #697 habe ich geschrieben, WARMUP decke einen geaenderten Zeitplan ab.
    Das war falsch: `first_on_default` nimmt den aeltesten Commit der Datei,
    und supervise.yml ist alt — nur ihr cron war neu. Die Invariante meldete
    daraufhin VERPASST fuer den 12:00-Takt, den GitHub nie gesehen hatte.
    """
    m = _modul()
    wann = m.takt_geaendert_am("supervise.yml", "0 */6 * * *")
    assert wann is not None, "kein Commit fuer den Takt gefunden"
    geburt = m.first_on_default("supervise.yml")
    if geburt:
        assert wann > geburt, (
            "der Takt muesste juenger sein als die Datei — sonst prueft dieser "
            "Test dasselbe wie vorher")


def test_unbekannter_takt_gibt_nichts():
    m = _modul()
    assert m.takt_geaendert_am("supervise.yml", "7 7 7 7 7") is None


def test_ein_geaenderter_takt_waermt_auf(monkeypatch, capsys):
    """Innerhalb des Fensters: WARMING, Wert 0, kein Befund."""
    m = _modul()
    now = dt.datetime(2026, 10, 9, 14, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(m, "token", lambda: "x")
    monkeypatch.setattr(m, "declared",
                        lambda: [("supervise.yml", ["0 */6 * * *"])])
    monkeypatch.setattr(m, "first_on_default", lambda n: dt.datetime(
        2026, 10, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "takt_geaendert_am", lambda n, s: dt.datetime(
        2026, 10, 9, 9, 31, tzinfo=dt.timezone.utc))
    # Letzter Lauf VOR der Taktaenderung — ohne das Fenster waere das VERPASST.
    monkeypatch.setattr(m, "newest_scheduled", lambda n: dt.datetime(
        2026, 10, 9, 9, 3, tzinfo=dt.timezone.utc))
    assert m._check(now) == 0
    aus = capsys.readouterr()
    assert "WARMING" in aus.err, aus.err
    assert "geaenderten Plans" in aus.err
    assert aus.out.strip().splitlines()[-1] == "0"


def test_nach_dem_fenster_wird_wieder_gemeldet(monkeypatch, capsys):
    """Das Fenster ist eine Frist, keine Dauerentschuldigung."""
    m = _modul()
    now = dt.datetime(2026, 10, 10, 2, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(m, "token", lambda: "x")
    monkeypatch.setattr(m, "declared",
                        lambda: [("supervise.yml", ["0 */6 * * *"])])
    monkeypatch.setattr(m, "first_on_default", lambda n: dt.datetime(
        2026, 10, 1, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "takt_geaendert_am", lambda n, s: dt.datetime(
        2026, 10, 9, 9, 31, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(m, "newest_scheduled", lambda n: dt.datetime(
        2026, 10, 9, 9, 3, tzinfo=dt.timezone.utc))
    assert m._check(now) == 1
    aus = capsys.readouterr()
    assert "VERPASST" in aus.err, aus.err


def test_check_external_runs_findet_seine_pakete_selbst():
    """Die Invarianten sind eine dritte Startstelle.

    Diese Datei wird nicht aus einer Crontab-Zeile gerufen, sondern aus der
    shell-Abfrage von c-external-schedule-fires — und lief nur, weil
    selftest.py aus einer Zeile mit globalem PYTHONPATH startet und seine
    Unterprozesse die Umgebung erben. Von 18 shell-Abfragen war genau diese
    eine betroffen; gefunden, nachdem PYTHONPATH weg war.
    """
    quelle = (WURZEL / "scripts" / "check_external_runs.py").read_text(
        encoding="utf-8")
    assert "sys.path.insert" in quelle
