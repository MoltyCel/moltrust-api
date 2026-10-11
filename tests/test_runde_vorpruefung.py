#!/usr/bin/env python3
"""Die acht Vorpruefungen H1 bis H8 — je zwei Gegenproben, je aus einem Fall.

Alle sechzehn sind `xfail(strict=True)`. Der Grund ist nicht Bequemlichkeit:
rote Tests ohne Marke blockieren jedes Merge, auch das der Umsetzung selbst,
und das Fenster bis zum 12.10. 16:00 UTC steht unter Aenderungsstopp. Mit
`strict=True` schlaegt die Marke um, sobald eine Pruefung traegt — der Test
wird dann rot, weil er besteht, und zwingt dazu, die Marke zu entfernen. Ein
offener Punkt, der sich nicht vergessen laesst.

Je Pruefung ein Fall, der rot sein muss, und einer, der durchgehen muss. Der
zweite ist der wichtigere: eine Pruefung, die nur an ihren Verletzungen
geprueft ist, ist geraten — dieselbe Regel wie bei einer Schwelle.

Die roten Faelle sind woertlich die des 11.10.2026, nicht erfundene.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import runde_vorpruefung as V  # noqa: E402

xfail = pytest.mark.xfail(strict=True, reason="H-Klasse ist Geruest, "
                                              "Umsetzung nach BQ1 und 14.10.")

# --- Die Textstuecke des 11.10., woertlich ---------------------------------

H1_ROT = ("After settlement nothing can be taken back: the finding is recorded "
          "in the result file, and that operator takes no place in later "
          "rounds.")
H1_GRUEN = (H1_ROT + "\nExcluded operators are listed in "
            "config/ausgeschlossene-betreiber.json, with the reason and a "
            "pointer to the result file that carries the finding.")

H2_ROT = ("This path grants no credits. You do not need any: every call in "
          "this task is free, and a registered DID gets 60 free calls an "
          "hour.")
H2_GRUEN = ("This path grants no credits, and you do not need any: none of "
            "the calls in this task is priced, so none of them touches a "
            "credit balance or the hourly credit allowance.")

H3_ROT = ("In round 4 we rejected on a criterion submitters could not see in "
          "advance and then could not tell them why.")
H3_GRUEN = ("In round 4 every unpaid submission failed a condition the task "
            "text had stated — ten paid places, filled by submission time, "
            "and one place per address across the round.")

H4_ROT = ("If a group turns out after the fact to have been one operator, its "
          "places go.")
H4_GRUEN = ("If we find before settlement that a group was one operator, its "
            "places go to the next group. After settlement nothing can be "
            "taken back: the finding is recorded in the result file.")

H5_ROT = ("The first three complete groups on this task, ranked by the time "
          "of the group's sixth endorsement.")
H5_GRUEN = (H5_ROT + "\n- all three submissions arrive inside the 72 hours; a "
            "group only counts once all three have submitted")

H6_ROT = '    "timestamp": "2026-10-10T12:00:00Z",'
H6_GRUEN = '    "timestamp": "<ISO-8601 UTC, the time of the interaction>",'


def stand(b):
    return b["stand"]


# --- H1 --------------------------------------------------------------------

@xfail
def test_h1_zusage_ohne_ort_ist_rot():
    """11.10.: die Ausschlussliste existierte nicht, als der Text sie zusagte."""
    assert stand(V.h1_zusage_ohne_ort(H1_ROT)) == V.ROT


@xfail
def test_h1_zusage_mit_ort_geht_durch():
    assert stand(V.h1_zusage_ohne_ort(H1_GRUEN)) == V.GRUEN


# --- H2 --------------------------------------------------------------------

@xfail
def test_h2_unbelegte_zahl_ist_rot():
    """Die 60 galten nur fuer acht bepreiste Endpunkte, keiner im Text."""
    assert stand(V.h2_zahl_ohne_messung(H2_ROT)) == V.ROT


@xfail
def test_h2_text_ohne_unbelegte_zahl_geht_durch():
    assert stand(V.h2_zahl_ohne_messung(H2_GRUEN)) == V.GRUEN


# --- H3 --------------------------------------------------------------------

@xfail
def test_h3_falsche_selbstanklage_ist_rot():
    """Alle 42 trugen platz_vergeben, und der r4-Text nannte die Bedingung."""
    assert stand(V.h3_selbstaussage_ohne_beleg(
        H3_ROT, beschreibungen={"r4": "Submissions past the tenth are "
                                      "qualified and unpaid."})) == V.ROT


@xfail
def test_h3_belegte_selbstaussage_geht_durch():
    assert stand(V.h3_selbstaussage_ohne_beleg(
        H3_GRUEN, beschreibungen={"r4": "The first ten submissions that "
                                        "satisfy every condition, by "
                                        "submission time. Submissions past "
                                        "the tenth are qualified and "
                                        "unpaid. One paid place per worker "
                                        "address across all three tasks "
                                        "together."})) == V.GRUEN


# --- H4 --------------------------------------------------------------------

@xfail
def test_h4_folge_nach_der_zahlung_ist_rot():
    """Eine Zahlung auf Base kommt nicht zurueck."""
    assert stand(V.h4_folge_ohne_deckung(H4_ROT)) == V.ROT


@xfail
def test_h4_getrennte_folge_geht_durch():
    assert stand(V.h4_folge_ohne_deckung(H4_GRUEN)) == V.GRUEN


# --- H5 --------------------------------------------------------------------

@xfail
def test_h5_reihung_ohne_frist_ist_rot():
    assert stand(V.h5_frist_fehlt(H5_ROT)) == V.ROT


@xfail
def test_h5_frist_und_reihung_gehen_durch():
    assert stand(V.h5_frist_fehlt(
        H5_GRUEN, felder={"duration": 72})) == V.GRUEN


# --- H6 --------------------------------------------------------------------

@xfail
def test_h6_zeitstempel_in_der_vergangenheit_ist_rot():
    jetzt = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)
    assert stand(V.h6_beispiel_in_der_vergangenheit(H6_ROT,
                                                    jetzt=jetzt)) == V.ROT


@xfail
def test_h6_platzhalter_geht_durch():
    jetzt = datetime.datetime(2026, 10, 11, tzinfo=datetime.timezone.utc)
    assert stand(V.h6_beispiel_in_der_vergangenheit(H6_GRUEN,
                                                    jetzt=jetzt)) == V.GRUEN


# --- H7 --------------------------------------------------------------------

@xfail
def test_h7_klasse_ohne_bedingung_im_text_ist_rot():
    """Eine Absage, die der Einreicher nicht vorhersehen konnte."""
    assert stand(V.h7_absageklasse_ohne_bedingung(
        "Three agents, six endorsements, inside 24 hours.",
        klassen={"betreiber_ausgeschlossen": "Ein Betreiber der Gruppe steht "
                                             "auf der Ausschlussliste"})) \
        == V.ROT


@xfail
def test_h7_jede_klasse_mit_entsprechung_geht_durch():
    assert stand(V.h7_absageklasse_ohne_bedingung(
        "fewer than six distinct endorsed pairs; two endorsements for the "
        "same pair count once",
        klassen={"zu_wenige_beitraege": "Weniger als sechs endorsierte "
                                        "Paare"})) == V.GRUEN


# --- H8 --------------------------------------------------------------------

@xfail
def test_h8_zwei_herleitungen_sind_rot():
    """11.10.: result.json sagte bezahlt 0, die Telegram-Zeile 1 bezahlt."""
    quelle = (
        "def ergebnis(...):\n"
        "    gewinner = gruppen_auswertung(spec, kept_c)['bezahlt']\n"
        "def markdown(...):\n"
        "    winners = kept_c[:spec['slots']]\n")
    assert stand(V.h8_zwei_herleitungen(modul=quelle)) == V.ROT


@xfail
def test_h8_eine_herleitung_geht_durch():
    quelle = (
        "def gruppen_alle(gathered, capped):\n"
        "    return {r: gruppen_auswertung(s, k) for ...}\n"
        "def ergebnis(..., gruppen_aus=None):\n"
        "    aus = gruppen_aus[ref]\n"
        "def markdown(...):\n"
        "    aus = gruppen_befund.get(ref)\n")
    assert stand(V.h8_zwei_herleitungen(modul=quelle)) == V.GRUEN


# --- Das Geruest selbst ----------------------------------------------------
# Diese vier laufen und muessen gruen sein: sie halten fest, dass das Geruest
# steht, dass es sich nicht fuer gruen ausgibt, und dass es nichts anlegt.

def test_alle_acht_sind_angelegt():
    assert [k for k, _fn in V.PRUEFUNGEN] == [f"H{i}" for i in range(1, 9)]


def test_jede_traegt_ihre_herkunft_im_quelltext():
    """Eine Pruefung ohne Herkunft ist beim naechsten Mal nicht nachpruefbar."""
    for kennung, fn in V.PRUEFUNGEN:
        doc = fn.__doc__ or ""
        assert "Herkunft" in doc, kennung
        assert "11.10.2026" in doc, kennung


def test_nicht_umgesetzt_ist_nicht_gruen():
    """Der Rueckfallwert zeigt nicht in die erlaubende Richtung."""
    befunde = V.pruefe("irgendein Text")
    assert len(befunde) == 8
    assert {b["stand"] for b in befunde} == {V.OFFEN}
    assert V.GRUEN not in {b["stand"] for b in befunde}


def test_main_legt_nichts_an_und_endet_ungleich_null(tmp_path):
    """Solange das Geruest steht, ist Exitcode 0 die Zustimmung, die es
    nicht gibt."""
    p = tmp_path / "text.txt"
    p.write_text("Form a trust triangle", encoding="utf-8")
    assert V.main([str(p)]) == 1


def test_eine_fehlende_datei_ist_kein_gruen(tmp_path):
    assert V.main([str(tmp_path / "gibtsnicht.txt")]) == 2


def test_das_modul_handelt_beim_import_nicht():
    """Eine Probe fuehrt keine Modulruempfe aus — sie liest sie."""
    import ast
    import pathlib

    baum = ast.parse(pathlib.Path(V.__file__).read_text(encoding="utf-8"))
    erlaubt = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
               ast.Assign, ast.AnnAssign, ast.Expr, ast.If)
    assert [type(k).__name__ for k in baum.body
            if not isinstance(k, erlaubt)] == []
    assert [k for k in baum.body
            if isinstance(k, ast.Expr) and isinstance(k.value, ast.Call)] == []


def test_es_haengt_nicht_im_stuendlichen_lauf():
    """Punkt E: heute nur das Geruest, kein Ausrollen und kein Cron.

    Die Invariantenklasse H wird nach dem Aenderungsstopp und nach dem Ablauf
    der Runde 5 eingehaengt, damit die laufende Runde ungestoert bleibt.
    """
    import pathlib

    wurzel = pathlib.Path(V.__file__).resolve().parent.parent
    inv = wurzel / "docs" / "invariants"
    h = sorted(p.name for p in inv.glob("h-*.yaml")) if inv.is_dir() else []
    assert h == [], f"H-Invarianten schon eingehaengt: {h}"
