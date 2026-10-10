#!/usr/bin/env python3
"""Die veroeffentlichten Zahlen der Runde 5 gegen den Code, der sie durchsetzt.

Eine Zahl in result.json, die der pruefende Code nicht kennt, ist eine Zusage
ohne Deckung. Und eine Zahl, die von der Vorgabe abweicht, ist eine andere
Runde als die beauftragte — beides faellt hier auf.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import gruppen as G  # noqa: E402
from scripts.runden_auswertung import bedingungen, ergebnis  # noqa: E402
from scripts.task_watch import RUNDEN  # noqa: E402

# Die Vorgabe des Auftrags vom 10.10.2026, als Zahlen. Weicht der Code ab,
# ist es eine andere Runde als die beauftragte.
VORGABE = {
    "laufzeit_stunden": 72,
    "praemie_brutto_mikro": 1_082_000,     # 1,082 USDC
    "fenster_stunden": 24,
    "deckel_je_betreiber": 3,
    "betreiber_je_gruppe": 3,
    "beitraege_je_gruppe": 6,
}


def test_die_vorgabe_steht_im_code():
    b = bedingungen("r5")
    assert b["laufzeit_stunden"] == VORGABE["laufzeit_stunden"]
    assert b["praemie_brutto_mikro"] == VORGABE["praemie_brutto_mikro"]
    assert b["gruppe"]["fenster_stunden"] == VORGABE["fenster_stunden"]
    assert b["gruppe"]["mitglieder"] == VORGABE["betreiber_je_gruppe"]
    assert b["gruppe"]["beitraege"] == VORGABE["beitraege_je_gruppe"]
    assert (b["gruppe"]["deckel_je_betreiber_in_gruppe"]
            == VORGABE["deckel_je_betreiber"])
    assert b["deckel_je_betreiber_je_runde"] == VORGABE["deckel_je_betreiber"]


def test_die_veroeffentlichten_zahlen_sind_die_geprueften():
    """Keine Abschrift: die Zahlen kommen aus dem pruefenden Modul."""
    g = bedingungen("r5")["gruppe"]
    assert g["mitglieder"] == G.MITGLIEDER
    assert g["beitraege"] == G.BEITRAEGE
    assert g["fenster_stunden"] == int(G.FENSTER.total_seconds() // 3600)
    assert g["deckel_je_betreiber_in_gruppe"] == G.DECKEL_JE_BETREIBER
    assert g["gruppen_je_aufgabe"] == G.GRUPPEN_JE_AUFGABE
    assert g["plaetze_je_aufgabe"] == G.GRUPPEN_JE_AUFGABE * G.MITGLIEDER


def test_die_praemie_geht_auf_zwei_aufgaben_auf():
    b = bedingungen("r5")
    je_aufgabe = b["praemie_brutto_mikro"] // b["aufgaben"]
    assert b["aufgaben"] == 2
    assert je_aufgabe == 541_000, "wie Runde 4: 0,541 je Aufgabe"
    assert je_aufgabe * b["aufgaben"] == b["praemie_brutto_mikro"]


def test_die_plaetze_passen_unter_die_zehn_des_vertrags():
    g = bedingungen("r5")["gruppe"]
    assert g["plaetze_je_aufgabe"] <= 10


def test_jede_grundklasse_der_pruefung_steht_in_der_datei():
    """Ein Grund, den die Pruefung vergeben kann und die Datei nicht
    erklaert, ist fuer einen Leser eine leere Zeichenkette."""
    g = bedingungen("r5")["gruppe"]
    assert set(g["grundklassen"]) == set(G.GRUENDE)


def test_ein_vorablauf_ohne_einreichungen_traegt_die_bedingungen():
    """Die Adresse muss antworten, bevor die Runde laeuft.

    Sonst zeigt die Aufgabenbeschreibung ab der ersten Minute auf eine 404,
    und die Zusage, dass jeder seinen Grund nachlesen kann, ist in dem
    Augenblick nicht wahr, in dem sie gelesen wird.
    """
    doc = ergebnis("r5", [], {}, {}, stand="offen")
    assert doc["runde"] == "r5"
    assert doc["stand"] == "offen"
    assert doc["bedingungen"]["fassung"] == "gruppen"
    assert doc["abgleich"] == {"einreichungen": 0, "bezahlt": 0,
                               "ohne_platz": 0, "abgewiesen": 0,
                               "ohne_aufgeschluesselten_grund": 0}
    assert doc["bezahlt"] == [] and doc["aufgaben"] == []


def test_runde_ohne_eintrag_hat_keine_bedingungen_statt_erfundener():
    assert bedingungen("r9") is None
    assert "r9" not in RUNDEN
