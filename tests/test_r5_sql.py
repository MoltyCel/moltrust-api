#!/usr/bin/env python3
"""Was aus einer Einreichung ins SQL darf — und was nicht.

Die beiden Abfragen der Gruppenauswertung bauen eine IN-Liste aus DIDs, und
die DIDs kommen aus den Deliverables der Worker. bandit B608 hat darauf
gezeigt, und zurecht: die erste Fassung verdoppelte Apostrophe von Hand, also
genau die Sorte Schutz, die funktioniert, bis sie es nicht tut.

Die `# nosec`-Begruendung im Quelltext sagt, dass jeder interpolierte Wert
`_DID_SQL` erfuellt hat und diese Form kein Anfuehrungszeichen zulaesst. Eine
Begruendung ohne Probe ist eine Behauptung — das hier ist die Probe.

Geprueft wird `sql_liste` und nicht die Datenbank: die Zusage betrifft, was in
die Zeichenkette gelangt, und nicht, was Postgres daraus macht.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.runden_auswertung import sql_liste  # noqa: E402

GUT = "did:moltrust:0123456789abcdef"

FEINDLICH = [
    "did:moltrust:0123456789abcdef'",
    "did:moltrust:0123456789abcde'",
    "' OR 1=1 --",
    "did:moltrust:'; DROP TABLE agents; --",
    "did:moltrust:0123456789abcdef'); DELETE FROM endorsements; --",
    'did:moltrust:0123456789abcde"',
    "did:moltrust:0123456789abcdef UNION SELECT key FROM api_keys",
    "did:moltrust:0123456789ABCDEF",      # gross: nicht die kanonische Form
    "did:moltrust:0123456789abcdefg",     # 17 Stellen
    "did:moltrust:0123456789abcde",       # 15 Stellen
    "did:moltrust:0123456789abcdez",      # kein Hex
    "did:web:example.com",
    "did:key:z6Mk",
    "",
    "   ",
    "did:moltrust:0123456789abcdef\n' OR 1=1",
    "did:moltrust:0123456789abcdef\x00",
    "did:moltrust:0123456789abcdef%",
    "did:moltrust:0123456789abcdef;",
]


def test_eine_gueltige_did_kommt_durch():
    assert sql_liste([GUT]) == f"'{GUT}'"


def test_keine_feindliche_eingabe_erreicht_das_sql():
    for roh in FEINDLICH:
        assert sql_liste([roh]) == "", f"durchgelassen: {roh!r}"


def test_eine_gute_neben_vielen_schlechten():
    """Eine schlechte Zeile darf die gute nicht mitnehmen und umgekehrt."""
    aus = sql_liste(FEINDLICH + [GUT])
    assert aus == f"'{GUT}'"


def test_in_der_ausgabe_steht_kein_zeichen_das_sql_beendet():
    aus = sql_liste(FEINDLICH + [GUT, "did:moltrust:" + "f" * 16])
    for zeichen in ('"', ";", "--", "/*", "%", "\\", "\n", "\x00", " "):
        assert zeichen not in aus, f"{zeichen!r} in {aus!r}"
    # Apostrophe kommen vor — als Begrenzer, und zwar genau zwei je Wert.
    assert aus.count("'") == 2 * (aus.count(",") + 1)


def test_doppelte_werden_einmal_genannt():
    assert sql_liste([GUT, GUT, GUT]) == f"'{GUT}'"


def test_die_reihenfolge_ist_festgelegt():
    """Sonst haengt die Abfrage an der Reihenfolge einer Menge."""
    a = "did:moltrust:" + "a" * 16
    b = "did:moltrust:" + "b" * 16
    assert sql_liste({b, a}) == sql_liste({a, b}) == f"'{a}','{b}'"


def test_leere_eingabe_gibt_leere_liste():
    assert sql_liste([]) == ""
    assert sql_liste(set()) == ""


def test_nicht_zeichenketten_fallen_weg():
    assert sql_liste([None, 17, {"did": GUT}, [GUT], GUT]) == f"'{GUT}'"


def test_die_leere_liste_fuehrt_zu_keiner_abfrage():
    """`IN ()` ist kein gueltiges SQL — die Aufrufer muessen vorher abbrechen."""
    from scripts import runden_auswertung as R

    gerufen = []
    alt = R.psql
    try:
        R.psql = lambda q: gerufen.append(q) or []
        assert R.merkmale_fuer(["' OR 1=1 --"]) == {}
        assert R.endorsements_fuer(["' OR 1=1 --"]) == []
    finally:
        R.psql = alt
    assert gerufen == [], "es wurde eine Abfrage gestellt"


def test_das_nosec_nennt_diese_datei():
    """Eine Begruendung, die auf eine Probe verweist, muss sie auch nennen."""
    import pathlib

    from scripts import runden_auswertung as R

    quelle = pathlib.Path(R.__file__).read_text(encoding="utf-8")
    marken = [z for z in quelle.splitlines() if "nosec B608" in z]
    assert len(marken) == 2, marken
    assert any("test_r5_sql.py" in z for z in marken)
    for z in marken:
        assert "sql_liste" in z, z
