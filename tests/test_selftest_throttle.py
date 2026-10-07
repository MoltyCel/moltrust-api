"""Die Drosselung sammelt Bekanntes und lässt Neues sofort durch.

Gemessen am 07.10.2026 über 48 Stunden: 50 Läufe, 50 Telegram-Nachrichten,
alle nach ALERTS, weil `a-track-record-burst` in jedem Lauf fehlschlug. Wer
fünfzig Mal dasselbe liest, liest beim einundfünfzigsten Mal nicht mehr.

Die Tests prüfen beide Richtungen jeder Regel — ein Register, das nur
schweigt, wäre nicht von einem kaputten zu unterscheiden.
"""
import datetime as dt
import json
import os

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


# ---------------------------------------------------------------------------
# Offene Befunde — das zweite Register
#
# Es haelt Befunde, die niemand erklaert hat: 72 h ruhig, hoechstens drei
# gleichzeitig, hoechstens eine Meldung je 24 h, danach Verfall und zurueck auf
# Sofortmeldung. Es wird nie mit bekannte-abweichungen.json vermischt — das
# eine ist erwartet und hat ein Gruen-Datum, das andere ist unerklaert und hat
# einen Verfall.
#
# Beide Richtungen je Regel: ein Register, das nur schweigt, waere von einem
# kaputten nicht zu unterscheiden.
# ---------------------------------------------------------------------------

def open_entry(**kw):
    base = {"invariante": "c-external-schedule-fires", "befund": "WARN",
            "eingetragen_am": "2026-10-07T12:00:00+00:00",
            "verfaellt_am": "2026-10-10T12:00:00+00:00",
            "gesehen": 1, "zuletzt_gemeldet": None}
    base.update(kw)
    return base


def open_path(tmp_path, entries=None, raw=None):
    p = tmp_path / "offene-befunde.json"
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
    else:
        p.write_text(json.dumps({"eintraege": entries or []}), encoding="utf-8")
    return str(p)


# -- load_open / save_open --------------------------------------------------

def test_offene_befunde_lesbar(tmp_path):
    assert len(t.load_open(open_path(tmp_path, [open_entry()]))) == 1


def test_fehlendes_offenes_register_ist_leer_nicht_kaputt(tmp_path):
    assert t.load_open(str(tmp_path / "gibtsnicht.json")) == []


def test_unlesbares_offenes_register_wirft(tmp_path):
    """Kaputt ist nicht leer. Ein Register, das sich nicht lesen laesst, darf
    nicht wie eines aussehen, in dem nichts steht."""
    with pytest.raises(Exception):
        t.load_open(open_path(tmp_path, raw="{kein json"))


def test_offener_eintrag_ohne_verfall_wird_abgelehnt(tmp_path):
    p = open_path(tmp_path, [{k: v for k, v in open_entry().items()
                              if k != "verfaellt_am"}])
    with pytest.raises(t.RegisterError) as exc:
        t.load_open(p)
    assert "verfaellt_am" in str(exc.value)


def test_gespeichertes_register_ist_nur_fuer_den_eigentuemer_lesbar(tmp_path):
    p = str(tmp_path / "offene-befunde.json")
    t.save_open([open_entry()], p)
    assert oct(os.stat(p).st_mode)[-3:] == "600"
    assert len(t.load_open(p)) == 1


# -- add_open ---------------------------------------------------------------

def test_erster_offener_eintrag_wird_angelegt(tmp_path):
    p = str(tmp_path / "offene-befunde.json")
    e = t.add_open("e-page-matches-source", "WARN", NOW, p)
    assert e["invariante"] == "e-page-matches-source"
    assert t._parse(e["verfaellt_am"]) - NOW == dt.timedelta(hours=t.OPEN_TTL_HOURS)
    assert len(t.load_open(p)) == 1


def test_derselbe_befund_zweimal_legt_keinen_zweiten_an(tmp_path):
    p = str(tmp_path / "offene-befunde.json")
    t.add_open("e-page-matches-source", "WARN", NOW, p)
    t.add_open("e-page-matches-source", "WARN", NOW + dt.timedelta(hours=1), p)
    assert len(t.load_open(p)) == 1


def test_dritter_eintrag_wird_angenommen(tmp_path):
    p = str(tmp_path / "offene-befunde.json")
    for i in range(t.OPEN_MAX):
        t.add_open(f"inv-{i}", "WARN", NOW, p)
    assert len(t.load_open(p)) == t.OPEN_MAX


def test_vierter_eintrag_wird_abgelehnt_und_verdraengt_nicht(tmp_path):
    """Der aelteste bleibt stehen. Ein Register, das den vierten aufnimmt und
    dafuer den ersten vergisst, verliert genau den Befund, der am laengsten
    unerklaert ist."""
    p = str(tmp_path / "offene-befunde.json")
    for i in range(t.OPEN_MAX):
        t.add_open(f"inv-{i}", "WARN", NOW, p)
    with pytest.raises(t.OpenRegisterFull) as exc:
        t.add_open("inv-neu", "WARN", NOW, p)
    assert "inv-neu" in str(exc.value)
    stehen = [e["invariante"] for e in t.load_open(p)]
    assert stehen == [f"inv-{i}" for i in range(t.OPEN_MAX)]
    assert "inv-neu" not in stehen


# -- open_due ---------------------------------------------------------------

def test_noch_nie_gemeldet_wird_gemeldet():
    assert t.open_due(open_entry(zuletzt_gemeldet=None), NOW) == "melden"


def test_vor_24_h_bleibt_still():
    e = open_entry(zuletzt_gemeldet=(NOW - dt.timedelta(hours=23)).isoformat())
    assert t.open_due(e, NOW) is None


def test_nach_24_h_wird_wieder_gemeldet():
    e = open_entry(zuletzt_gemeldet=(NOW - dt.timedelta(hours=24)).isoformat())
    assert t.open_due(e, NOW) == "melden"


def test_nach_72_h_verfaellt():
    e = open_entry(verfaellt_am=(NOW - dt.timedelta(minutes=1)).isoformat())
    assert t.open_due(e, NOW) == "verfallen"


def test_verfallen_schlaegt_die_24_h_ruhe():
    """Nach dem Verfall meldet jeder Lauf, auch wenn gerade erst gemeldet
    wurde — das ist der Unterschied zwischen ruhig und erledigt."""
    e = open_entry(verfaellt_am=(NOW - dt.timedelta(hours=1)).isoformat(),
                   zuletzt_gemeldet=(NOW - dt.timedelta(minutes=5)).isoformat())
    assert t.open_due(e, NOW) == "verfallen"


# -- open_line --------------------------------------------------------------

def test_meldung_nennt_zaehler_und_verfall():
    line = t.open_line(open_entry(gesehen=7))
    assert "c-external-schedule-fires WARN" in line
    assert "7x seit letzter Meldung" in line
    assert "unerklärt" in line
    assert "10.10. 12:00Z" in line


# -- digest_line ------------------------------------------------------------

def test_sammelzeile_ohne_offene_nennt_keine():
    line = t.digest_line(NOW, 14, 1, [], [], None)
    assert "offen" not in line
    assert line.startswith("Selftest 21:00Z — 14 Läufe, 1 neue Befunde, 0 bekannt")


def test_sammelzeile_haelt_offen_und_bekannt_auseinander():
    """Beide Gruppen stehen getrennt. Zusammengezaehlt waere der Unterschied
    weg, auf den die zwei Register gebaut sind."""
    bekannt = [{"id": "a-track-record-burst", "status": "FAIL",
                "eintrag": entry()}]
    line = t.digest_line(NOW, 14, 0, bekannt, [], [open_entry()])
    assert "1 bekannt" in line
    assert "1 offen" in line
    assert "a-track-record-burst grün 09.10. 02:33Z" in line
    assert "c-external-schedule-fires, verfällt 10.10. 12:00Z" in line
    # Die offene Gruppe steht hinter der bekannten, nicht darin.
    assert line.index("bekannt") < line.index("offen")


def test_sammelzeile_zaehlt_offene_nicht_zu_bekannt():
    bekannt = [{"id": "a-track-record-burst", "status": "FAIL",
                "eintrag": entry()}]
    mit = t.digest_line(NOW, 14, 0, bekannt, [], [open_entry()])
    ohne = t.digest_line(NOW, 14, 0, bekannt, [], None)
    assert "1 bekannt" in mit and "1 bekannt" in ohne


def test_zwei_benannte_registerfehler_nicht_einer():
    """Zwei Register, zwei Meldungen. Eine gemeinsame Zeile liesse offen,
    welches der beiden nicht lesbar war — und genau das ist die Auskunft, die
    man in dem Moment braucht."""
    src = open("scripts/selftest.py", encoding="utf-8").read()
    assert "[REGISTER] bekannte-abweichungen nicht lesbar:" in src
    assert "[REGISTER] offene-befunde nicht lesbar:" in src
    # Das Negativ: die alte, namenlose Fassung darf nicht daneben stehen.
    assert "[REGISTER] nicht lesbar:" not in src
