#!/usr/bin/env python3
"""Die Sammelmeldung: je Punkt eine Ausloese- und eine Schweigeprobe.

Die Schweigeprobe ist die wichtigere. Eine Pruefung, die nur an ihren
Verletzungen geprueft ist, ist geraten — und eine Meldung, die bei jedem Lauf
etwas sagt, wird nicht mehr gelesen.

Alle fuenf Faelle sind die des 12.10.2026, nicht erfundene.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import selftest_throttle as T  # noqa: E402

UTC = dt.timezone.utc
JETZT = dt.datetime(2026, 10, 12, 21, 0, tzinfo=UTC)


def offener(name, verfaellt, befund="FAIL"):
    return {"invariante": name, "befund": befund,
            "eingetragen_am": (verfaellt - dt.timedelta(hours=72)
                               ).isoformat(timespec="seconds"),
            "verfaellt_am": verfaellt.isoformat(timespec="seconds"),
            "gesehen": 1, "zuletzt_gemeldet": None}


# =========================================================================
# 1) Verfallener offener Befund: Sofortmeldung UEBERFAELLIG und Austritt
# =========================================================================

def test_1_ausloese_verstrichener_verfall_wird_ueberfaellig():
    """Der Fall des 12.10.: zwei Eintraege standen 48 h ueber ihrem Verfall.

    Die alte Pruefung hing an der Schleife ueber die Befunde DIESES Laufs und
    erreichte sie nie — beide waren Prosa-Befunde ohne Invariante und traten
    nicht wieder auf.
    """
    e = offener("api-checkout-verschmutzt-blockiert-jeden-deploy",
                JETZT - dt.timedelta(hours=48))
    raus = T.expired_open([e], JETZT)
    assert len(raus) == 1
    assert "verstrichen" in raus[0]["_grund"]
    zeile = T.ueberfaellig_line(raus[0])
    assert "UEBERFAELLIG" in zeile
    assert "Austritt aus dem Register" in zeile
    assert "72 h ohne Erklaerung" in zeile


def test_1_schweigen_laufender_eintrag_bleibt():
    """Ein Eintrag innerhalb seiner 72 h wird nicht angefasst."""
    e = offener("c-irgendwas", JETZT + dt.timedelta(hours=5))
    assert T.expired_open([e], JETZT) == []


def test_1_austritt_entfernt_nur_den_genannten(tmp_path):
    p = tmp_path / "offene-befunde.json"
    bleibt = offener("bleibt", JETZT + dt.timedelta(hours=5))
    geht = offener("geht", JETZT - dt.timedelta(hours=1))
    T.save_open([bleibt, geht], str(p))
    assert T.drop_from_open(["geht"], str(p)) == 1
    uebrig = [e["invariante"] for e in T.load_open(str(p))]
    assert uebrig == ["bleibt"]


def test_1_schweigen_austritt_ohne_namen_schreibt_nicht(tmp_path):
    p = tmp_path / "offene-befunde.json"
    T.save_open([offener("bleibt", JETZT + dt.timedelta(hours=5))], str(p))
    vorher = p.read_bytes()
    assert T.drop_from_open([], str(p)) == 0
    assert p.read_bytes() == vorher


def test_1_unlesbarer_verfall_gilt_als_verstrichen():
    """Ein Eintrag, dessen Frist niemand lesen kann, stellt nichts ruhig."""
    e = offener("kaputt", JETZT)
    e["verfaellt_am"] = "irgendwann"
    raus = T.expired_open([e], JETZT)
    assert len(raus) == 1
    assert "nicht lesbar" in raus[0]["_grund"]


# =========================================================================
# 2) Neue Befunde mit Namen, nicht als Zahl
# =========================================================================

def test_2_ausloese_neue_befunde_stehen_mit_namen():
    z = T.digest_line(JETZT, 12,
                      [("c-context-urls-resolve", "FAIL"),
                       ("c-external-schedule-fires", "WARN")],
                      [], [], None)
    assert "2 neue Befunde" in z
    assert "c-context-urls-resolve FAIL" in z
    assert "c-external-schedule-fires WARN" in z


def test_2_schweigen_kein_befund_nennt_keinen_namen():
    z = T.digest_line(JETZT, 12, [], [], [], None)
    assert "0 neue Befunde" in z
    assert "(" not in z.split("—")[1].split(",")[0]


def test_2_eine_zahl_bleibt_erlaubt():
    """Aeltere Laufberichte tragen keine Namen. Dann ist die Zahl die
    Aussage, und sie wird nicht erfunden."""
    z = T.digest_line(JETZT, 12, 3, [], [], None)
    assert "3 neue Befunde" in z


# =========================================================================
# 3) Autofix zaehlt nur Korrekturen
# =========================================================================

def test_3_ausloese_eine_korrektur_wird_gezaehlt():
    korr, ohne = T.autofix_zaehlen(["rerun_registry_export: ok (Lauf 1 von 3)"])
    assert korr == {"rerun_registry_export": 1}
    assert ohne == 0
    z = T.digest_line(JETZT, 1, [], [], sorted(korr.items()), None,
                      autofix_ohne=ohne)
    assert "1x korrigiert (rerun_registry_export)" in z
    assert "grün" not in z


def test_3_schweigen_none_ist_kein_autofix():
    """Der Fall des 12.10.: "none" stand 267 Mal in den Laufberichten und
    waere als "267x grün (none)" gemeldet worden — Reparaturen, die es nie
    gab."""
    korr, ohne = T.autofix_zaehlen(["none"] * 267)
    assert korr == {}
    assert ohne == 0, "none ist kein Lauf, der zu zaehlen waere"
    z = T.digest_line(JETZT, 12, [], [], sorted(korr.items()), None,
                      autofix_ohne=ohne)
    assert "none" not in z
    assert "keine Autofixe" in z


def test_3_ohne_eingriff_steht_eigen_und_nicht_gruen():
    korr, ohne = T.autofix_zaehlen([
        "waere gelaufen: rerun_registry_export (dry-run)",
        "Deckel erreicht (3 je 24 h), Autofix ausgesetzt",
        "nicht ausgefuehrt: foo steht nicht in GREEN_FIXES",
        "retry_once",
    ])
    assert korr == {}
    assert ohne == 4
    z = T.digest_line(JETZT, 4, [], [], sorted(korr.items()), None,
                      autofix_ohne=ohne)
    assert "4 Läufe ohne Eingriff" in z
    assert "korrigiert" not in z


def test_3_fehlgeschlagen_ist_keine_korrektur():
    korr, ohne = T.autofix_zaehlen([
        "rerun_registry_export: fehlgeschlagen (Lauf 2 von 3)"])
    assert korr == {}
    assert ohne == 1


def test_3_korrektur_und_ohne_eingriff_nebeneinander():
    korr, ohne = T.autofix_zaehlen([
        "rerun_registry_export: ok (Lauf 1 von 3)", "none", "retry_once"])
    z = T.digest_line(JETZT, 3, [], [], sorted(korr.items()), None,
                      autofix_ohne=ohne)
    assert "1x korrigiert" in z and "1 Lauf ohne Eingriff" in z


# =========================================================================
# 4) Der Web-Root-Ausgangsstand ist benannt
# =========================================================================

def test_4_ausloese_ohne_begruendung_sagt_die_meldung_es():
    from scripts import webroot_drift as W
    z = W.baseline_lines({"untracked": [1, 2], "drift": {"a": "b"}})
    assert len(z) == 1
    assert "ohne Begruendung" in z[0]


def test_4_schweigen_mit_begruendung_bleibt_es_eine_zeile():
    """Mein erster Entwurf schrieb die Gruppen als eigene Zeilen in jede
    Meldung. `test_quiet_day_is_one_line_with_the_baseline_counted` hat das
    abgewiesen — ein statischer Umstand, der zweimal taeglich zwei Zeilen
    kostet, wird nach einer Woche nicht mehr gelesen. Der Verweis steht
    jetzt in der Kopfzeile, die Namen in der Datei.
    """
    from scripts import webroot_drift as W
    b = {"untracked": [1] * 16,
         "begruendung": {
             "ohne_commit": [{"gruppe": "Archiv des Mai-Umbaus", "zahl": 16,
                              "grund": "bewusst"}],
             "abweichend": [{"pfad": ".gitignore", "grund": "alt"}]}}
    assert W.baseline_lines(b) == [], "ein ruhiger Ausgangsstand schweigt"
    hinweis = W.baseline_hinweis(b)
    assert "config/webroot_baseline.json" in hinweis
    assert "1 Gruppen" in hinweis and "1 Pfade" in hinweis


def test_4_eine_gruppe_ohne_grund_wird_genannt():
    """Eine Begruendung ohne Grund ist keine."""
    from scripts import webroot_drift as W
    z = W.baseline_lines({"untracked": [1] * 16, "begruendung": {
        "ohne_commit": [{"gruppe": "Archiv", "zahl": 16}],
        "abweichend": []}})
    assert any("ohne Grund im Ausgangsstand" in x for x in z)
    assert any("Archiv" in x for x in z)


def test_4_eine_summe_die_nicht_aufgeht_wird_genannt():
    from scripts import webroot_drift as W
    z = W.baseline_lines({"untracked": [1] * 5, "begruendung": {
        "ohne_commit": [{"gruppe": "Archiv", "zahl": 16, "grund": "x"}],
        "abweichend": []}})
    assert any("geht nicht auf" in x for x in z)


def test_4_die_echte_datei_ist_begruendet_und_geht_auf():
    """Die 28 und die abweichenden sind benannt, und die Summen stimmen.

    Abweichend waren am 12.10. drei; registry-proof.py (ops W1) und
    docs/caep.html (ops W2) sind seit dem 14.10. gleich dem Repo.
    """
    import pathlib
    p = (pathlib.Path(__file__).resolve().parent.parent
         / "config" / "webroot_baseline.json")
    doc = json.loads(p.read_text(encoding="utf-8"))
    b = doc["begruendung"]
    # 28 seit TA-SPLIT (27.10.): trouvart/index.html bis zum Rueckbau am 04.11.
    assert sum(g["zahl"] for g in b["ohne_commit"]) == 28
    assert {a["pfad"] for a in b["abweichend"]} == set(doc["drift"]) == {".gitignore"}
    for g in b["ohne_commit"]:
        assert g.get("grund"), g
    for a in b["abweichend"]:
        assert a.get("grund"), a


# =========================================================================
# 5) Der Widerspruch: ein verfallener Befund erreicht die Meldung nicht
# =========================================================================

def test_5_ausloese_der_widerspruch_vom_12_10():
    """Beide Zahlen gleichzeitig: "JEDER Deploy blockiert" neben 19 ok.

    Ohne Verfall nach der Uhr stand der Befund in `offen` und damit in der
    Zeile, waehrend die Deploy-Zeile derselben Meldung 19 erfolgreiche
    Deploys nannte.
    """
    e = offener("api-checkout-verschmutzt-blockiert-jeden-deploy",
                JETZT - dt.timedelta(hours=48))
    z = T.digest_line(JETZT, 12, [], [], [], [e])
    assert "1 offen" in z
    assert "api-checkout-verschmutzt" in z


def test_5_schweigen_nach_dem_austritt_steht_er_nicht_mehr_drin():
    e = offener("api-checkout-verschmutzt-blockiert-jeden-deploy",
                JETZT - dt.timedelta(hours=48))
    uebrig = [x for x in [e]
              if x not in T.expired_open([e], JETZT)
              and x["invariante"] not in
              {y["invariante"] for y in T.expired_open([e], JETZT)}]
    z = T.digest_line(JETZT, 12, [], [], [], uebrig)
    assert "offen" not in z
    assert "api-checkout-verschmutzt" not in z


def test_5_ein_gueltiger_offener_befund_steht_weiter_drin():
    """Der Austritt trifft nur Verfallene — sonst waere das Register
    wirkungslos."""
    e = offener("c-noch-in-untersuchung", JETZT + dt.timedelta(hours=10))
    assert T.expired_open([e], JETZT) == []
    z = T.digest_line(JETZT, 12, [], [], [], [e])
    assert "1 offen" in z and "c-noch-in-untersuchung" in z


def test_3_einzahl_und_mehrzahl():
    """Ein Lauf, nicht "1 Läufe"."""
    z1 = T.digest_line(JETZT, 1, [], [], [], None, autofix_ohne=1)
    z2 = T.digest_line(JETZT, 2, [], [], [], None, autofix_ohne=2)
    assert "1 Lauf ohne Eingriff" in z1
    assert "Läufe" not in z1, z1          # auch die Lauf-Zahl, nicht nur Autofix
    assert "1 Lauf," in z1
    assert "2 Läufe ohne Eingriff" in z2
    assert "2 Läufe," in z2


def test_3_das_wort_gruen_steht_nicht_am_autofix():
    """Der Abnahmetest des 12.10. suchte "grün" in der ganzen Zeile und fand
    es im Erwartungsregister ("... grün 14.10."). Geprueft wird der
    Autofix-Teil, nicht die Zeile."""
    z = T.digest_line(JETZT, 1, [], [], [("rerun_registry_export", 1)], None)
    teil = z[z.index("Autofix:"):]
    assert "korrigiert" in teil
    assert "grün" not in teil
