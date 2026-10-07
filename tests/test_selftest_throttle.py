"""Die Drosselung sammelt Bekanntes und lässt Neues sofort durch.

Gemessen am 07.10.2026 über 48 Stunden: 50 Läufe, 50 Telegram-Nachrichten,
alle nach ALERTS, weil `a-track-record-burst` in jedem Lauf fehlschlug. Wer
fünfzig Mal dasselbe liest, liest beim einundfünfzigsten Mal nicht mehr.

Die Tests prüfen beide Richtungen jeder Regel — ein Register, das nur
schweigt, wäre nicht von einem kaputten zu unterscheiden.
"""
import datetime as dt
import json

import pytest

from scripts import selftest_throttle as t

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 7, 21, 0, tzinfo=UTC)


def entry(**kw):
    base = {"invariante": "a-track-record-burst", "befund": "FAIL",
            "grund": "ein Satz", "gruen_erwartet": "2026-10-09T02:33:55Z",
            "eingetragen_am": "2026-10-07T20:00:00Z"}
    base.update(kw)
    return base


def write(tmp_path, entries):
    p = tmp_path / "bekannte-abweichungen.json"
    p.write_text(json.dumps({"eintraege": entries}), encoding="utf-8")
    return str(p)


# --- Register ---------------------------------------------------------------

def test_fehlendes_gruen_erwartet_wird_nicht_angenommen(tmp_path):
    """Kein Standardwert. Ein Eintrag ohne Ablauf ist Dauerstumm."""
    p = write(tmp_path, [{k: v for k, v in entry().items() if k != "gruen_erwartet"}])
    with pytest.raises(t.RegisterError) as exc:
        t.load_register(p)
    assert "gruen_erwartet" in str(exc.value)


def test_vollstaendiger_eintrag_wird_angenommen(tmp_path):
    assert len(t.load_register(write(tmp_path, [entry()]))) == 1


def test_unbekannter_befundwert_wird_abgelehnt(tmp_path):
    with pytest.raises(t.RegisterError):
        t.load_register(write(tmp_path, [entry(befund="GELB")]))


def test_fehlendes_register_ist_leer_nicht_kaputt(tmp_path):
    assert t.load_register(str(tmp_path / "gibtsnicht.json")) == []


# --- Einordnung -------------------------------------------------------------

def test_bekannter_befund_meldet_nicht():
    c = t.classify([{"id": "a-track-record-burst", "status": "FAIL"}],
                   [entry()], NOW)
    assert c["neu"] == [] and len(c["bekannt"]) == 1 and c["verfallen"] == []


def test_unbekannter_befund_geht_sofort_raus():
    c = t.classify([{"id": "a-write-endpoint-flood", "status": "WARN"}],
                   [entry()], NOW)
    assert len(c["neu"]) == 1


def test_verschaerfter_befund_gilt_als_neu():
    """WARN im Register, FAIL im Lauf: das Register kennt den anderen Zustand."""
    c = t.classify([{"id": "c-crontab-snapshot-matches", "status": "FAIL"}],
                   [entry(invariante="c-crontab-snapshot-matches", befund="WARN",
                          gruen_erwartet="PR #351")], NOW)
    assert len(c["neu"]) == 1 and c["bekannt"] == []


def test_verstrichenes_datum_verfaellt():
    c = t.classify([{"id": "a-track-record-burst", "status": "FAIL"}],
                   [entry(gruen_erwartet="2026-10-06T00:00:00Z")], NOW)
    assert len(c["verfallen"]) == 1
    assert "verstrichen" in c["verfallen"][0]["grund"]


def test_pr_eintrag_verfaellt_nicht_ohne_token():
    """Eine unbeantwortete Frage ist kein Verfall."""
    c = t.classify([{"id": "c-crontab-snapshot-matches", "status": "WARN"}],
                   [entry(invariante="c-crontab-snapshot-matches", befund="WARN",
                          gruen_erwartet="PR #351")], NOW, token="")
    assert c["verfallen"] == [] and len(c["bekannt"]) == 1


def test_unlesbares_gruen_erwartet_verfaellt():
    c = t.classify([{"id": "a-track-record-burst", "status": "FAIL"}],
                   [entry(gruen_erwartet="bald")], NOW)
    assert len(c["verfallen"]) == 1


def test_alter_eintrag_wird_markiert_aber_bleibt_gueltig():
    alt = entry(eingetragen_am="2026-09-01T00:00:00Z")
    c = t.classify([{"id": "a-track-record-burst", "status": "FAIL"}], [alt], NOW)
    assert len(c["bekannt"]) == 1 and c["bekannt"][0]["veraltet"] is True


# --- Takt der Sammelmeldung -------------------------------------------------

@pytest.mark.parametrize("stunde,erwartet", [(8, "2026-10-07T08"), (21, "2026-10-07T20"),
                                             (7, "2026-10-06T20")])
def test_faelliger_takt(stunde, erwartet):
    now = dt.datetime(2026, 10, 7, stunde, 5, tzinfo=UTC)
    slot = t.due_digest_slot(now, {})
    assert slot and slot[0] == erwartet


def test_gesendeter_takt_wiederholt_sich_nicht():
    now = dt.datetime(2026, 10, 7, 21, 0, tzinfo=UTC)
    assert t.due_digest_slot(now, {"2026-10-07T20": "x"}) is None


# --- Die Zeile --------------------------------------------------------------

def test_zeile_bei_null_neuen_befunden():
    line = t.digest_line(dt.datetime(2026, 10, 7, 8, tzinfo=UTC), 12, 0, [], [])
    assert line.startswith("Selftest 08:00Z — 12 Läufe, 0 neue Befunde, 0 bekannt")


def test_zeile_nennt_bekannte_und_autofix():
    bekannt = [
        {"id": "a-track-record-burst", "status": "FAIL", "eintrag": entry()},
        {"id": "c-crontab-snapshot-matches", "status": "WARN",
         "eintrag": entry(invariante="c-crontab-snapshot-matches", befund="WARN",
                          gruen_erwartet="PR #351")},
        {"id": "c-external-schedule-fires", "status": "WARN",
         "eintrag": entry(invariante="c-external-schedule-fires", befund="WARN",
                          gruen_erwartet="PR #351")},
    ]
    line = t.digest_line(dt.datetime(2026, 10, 7, 8, tzinfo=UTC), 12, 0, bekannt,
                         [("regenerate_feed", 1)])
    assert "3 bekannt" in line
    assert "a-track-record-burst grün 09.10. 02:33Z" in line
    assert "2x WARN an PR #351" in line, line
    assert "Autofix: 1x grün (regenerate_feed)" in line


# --- Entfernen --------------------------------------------------------------

def test_verfallener_eintrag_faellt_aus_dem_register(tmp_path):
    p = write(tmp_path, [entry(), entry(invariante="c-external-schedule-fires",
                                        befund="WARN", gruen_erwartet="PR #351")])
    assert t.drop_from_register(["a-track-record-burst"], p) == 1
    rest = t.load_register(p)
    assert [e["invariante"] for e in rest] == ["c-external-schedule-fires"]


def test_entfernen_ohne_treffer_aendert_nichts(tmp_path):
    p = write(tmp_path, [entry()])
    assert t.drop_from_register(["gibt-es-nicht"], p) == 0
    assert len(t.load_register(p)) == 1
