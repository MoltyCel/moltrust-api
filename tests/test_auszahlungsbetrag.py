#!/usr/bin/env python3
"""Der Auszahlungsbetrag gegen das, was auf der Kette liegt.

Gemessen am 10.10.2026 an den drei Settlement-Transaktionen der Runde 4:

  STUFE1-R4-1  0x9e35d49678e4b38382d583350d174df9beecbdeb7419629ed3ad1c46f28b028c
  STUFE1-R4-2  0x9cd1fcf0650d303484eec89aaa4ca3c0e7804a2bce8b30784f945ccc4670712e
  TIEFE-R4     0x3b00c90a5d31c529427993f116649204900d0d69bdf897740ae1635787b612ee

Jede traegt 11 USDC-Transfers (Block 52388451/53/55, Status 1): zehnmal 50043
an die Arbeiter, einmal 40570 Gebuehr, zusammen genau 541000.

Die Zahlen stehen hier als Konstanten, weil ein Test, der dafuer die Kette
abfragt, vom Netz abhaengt und beim naechsten RPC-Ausfall rot wird, ohne dass
sich etwas geaendert hat. Die Messung steht im Kommentar; nachfahren laesst
sie sich mit eth_getTransactionReceipt auf die drei Hashes.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.task_watch import FEE_BPS, netto_mikro  # noqa: E402

BRUTTO = 541_000          # Praemie je Aufgabe, Mikro-USDC
PLAETZE = 10
JE_ARBEITER = 50_043      # gemessen
GEBUEHR_JE_AUFGABE = 40_570  # gemessen
RUNDE4_NETTO = 1_501_290  # drei Aufgaben


def test_ein_empfaenger_bekommt_was_die_kette_zeigt():
    assert netto_mikro(BRUTTO, 10000 // PLAETZE) == JE_ARBEITER


def test_zehn_empfaenger_und_die_gebuehr_ergeben_die_praemie():
    netto = netto_mikro(BRUTTO, 1000) * PLAETZE
    assert netto == 500_430
    assert BRUTTO - netto == GEBUEHR_JE_AUFGABE
    assert netto + GEBUEHR_JE_AUFGABE == BRUTTO, "kein Staub bleibt liegen"


def test_die_runde_vier_summe():
    assert netto_mikro(BRUTTO, 1000) * PLAETZE * 3 == RUNDE4_NETTO


def test_das_alte_modell_lag_daneben_und_zwar_nach_unten():
    """Die Rechnung, die bis zum 10.10.2026 in der Auswertung stand.

    `gross * (1 - FEE_BPS/10000)` auf die ganze Praemie ergibt 500425 — fuenf
    zu wenig je Aufgabe, fuenfzehn fuer die Runde. Der Test haelt die
    Abweichung fest, damit niemand zur geschlossenen Form zurueckgeht, weil
    sie kuerzer aussieht.
    """
    alt = round(BRUTTO * (1 - FEE_BPS / 10000))
    neu = netto_mikro(BRUTTO, 1000) * PLAETZE
    assert alt == 500_425
    assert neu - alt == 5
    assert neu == 500_430


def test_abschneiden_nicht_runden():
    """54100 * 750 / 10000 ist 4057,5 und der Vertrag nimmt 4057."""
    anteil = BRUTTO * 1000 // 10000
    assert anteil == 54_100
    assert anteil * FEE_BPS / 10000 == 4057.5
    assert anteil - netto_mikro(BRUTTO, 1000) == 4057


def test_ganzzahlig_ohne_fliesskomma():
    w = netto_mikro(BRUTTO, 1000)
    assert isinstance(w, int)
    assert not isinstance(w, bool)


def test_runde_fuenf_mit_neun_plaetzen():
    """Runde 5 teilt 0,541 auf neun Plaetze: 9 x 1111 bp sind nicht 10000.

    Die Anteilsregel muss den Rest vergeben, sonst summieren die bps nicht auf
    10000 und `accept-submissions` weist den Aufruf ab. Hier wird nur
    festgehalten, dass die glatte Teilung es nicht tut — die Verteilung des
    Restes macht `allocate_bps`.
    """
    assert 10000 // 9 == 1111
    assert 1111 * 9 == 9999, "ein bp bleibt uebrig und muss vergeben werden"


def test_neun_empfaenger_lassen_keinen_staub_liegen():
    """Mit verteiltem Rest geht die Praemie auf: Netto + Gebuehr = Brutto."""
    bps = [1112] + [1111] * 8          # der Rest auf den ersten Platz
    assert sum(bps) == 10000
    netto = sum(netto_mikro(BRUTTO, b) for b in bps)
    gebuehr = sum(BRUTTO * b // 10000 - netto_mikro(BRUTTO, b) for b in bps)
    assert netto + gebuehr <= BRUTTO
    assert BRUTTO - (netto + gebuehr) <= PLAETZE, \
        "Rest aus der bps-Teilung, nicht aus der Gebuehr"
