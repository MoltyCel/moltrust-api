"""Jede Agentenzahl der Tagesmeldung traegt Definition und Stichtag (ops Z1).

Jede Regel zweimal: der Fall, der die Pruefung ausloest, und der Normalbetrieb,
der schweigen muss. Der zweite Teil ist der, der eine geratene Pruefung findet
-- eine Pruefung, die bei jeder Nachricht rot ist, wird stummgeschaltet und
meldet danach auch den Fall nicht mehr, fuer den sie gebaut wurde.

Keine Datenbank, kein App-Import: hier laufen reine Funktionen ueber Werte, die
der Aufrufer mitbringt.
"""
import os
import re
import subprocess
import sys

from app import zahl_deklaration as zd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WERTE = {
    "registered": 602,
    "active": 341,
    "test": 23,
    "partner_test": 31,
    "new_12h": "+0",
    "funnel_7d": "+4 nach own_test 4",
}
STICHTAG = "2026-10-17 07:00 UTC"
FENSTER = 30

# Die Fassung, die vom 14.10. bis zum 16.10.2026 fuenf Laeufe lang rausging.
ALT = """Good morning — MolTrust Stats

Agents: 598 registered / 341 active (30d) / 19 test / 31 partner-test
New (12h): +0
Credentials: 734
"""


def nachricht(block: str) -> str:
    return f"Good morning — MolTrust Stats\n\n{block}\nCredentials: 739\n"


# --- Schweigeproben ---------------------------------------------------------

def test_gerenderter_block_ist_sauber():
    block = zd.block(WERTE, STICHTAG, FENSTER)
    assert zd.undeklarierte(nachricht(block)) == []


def test_fenster_muss_zum_rendern_nicht_bekannt_sein():
    """Die Pruefung laeuft auch, wenn sie die gemessene Fensterbreite nicht hat.

    Sie steht in agent_counts.sql und waechst mit den Daten. Eine Pruefung, die
    sie kennen muesste, waere an dem Tag rot, an dem das Fenster waechst.
    """
    block = zd.block(WERTE, STICHTAG, 27)
    assert zd.undeklarierte(block + "\nCredentials: 1\n") == []


def test_fehlende_zahl_laesst_nur_ihre_zeile_weg():
    ohne = {k: v for k, v in WERTE.items() if k != "funnel_7d"}
    block = zd.block(ohne, STICHTAG, FENSTER)
    assert "funnel" not in block
    assert zd.undeklarierte(nachricht(block)) == []


def test_zahlen_hinter_dem_block_zaehlen_nicht_mit():
    """Credits, MCP und Zahlungen sind keine Agentenzahlen."""
    block = zd.block(WERTE, STICHTAG, FENSTER)
    weiter = nachricht(block) + "\nMCP Traffic (12h):\n  Total: 1236\n  Unique IPs: 4\n"
    assert zd.undeklarierte(weiter) == []


# --- Ausloeseproben ---------------------------------------------------------

def test_alte_fassung_wird_rot():
    befunde = zd.undeklarierte(ALT)
    assert befunde, "die Fassung vom 14.10. muss einen Befund erzeugen"
    assert any("kein Block" in b for b in befunde)


def test_zahl_ohne_definition_wird_rot():
    block = zd.block(WERTE, STICHTAG, FENSTER)
    verstuemmelt = re.sub(r"^(  registered 602) — .*$", r"\1", block, flags=re.MULTILINE)
    befunde = zd.undeklarierte(nachricht(verstuemmelt))
    assert any("ohne Definition" in b for b in befunde), befunde


def test_unbekanntes_etikett_wird_rot():
    """Eine neue Zahl ohne Eintrag in DEKLARATIONEN ist ein Befund."""
    block = zd.block(WERTE, STICHTAG, FENSTER)
    erweitert = block + "\n  renewals 17 — irgendwas"
    befunde = zd.undeklarierte(nachricht(erweitert))
    assert any("renewals" in b and "DEKLARATIONEN" in b for b in befunde), befunde


def test_kopf_ohne_stichtag_wird_rot():
    block = zd.block(WERTE, STICHTAG, FENSTER)
    ohne = block.replace(f"Stichtag {STICHTAG}, ", "")
    befunde = zd.undeklarierte(nachricht(ohne))
    assert any("ohne Stichtag" in b for b in befunde), befunde


def test_stichtag_ohne_uhrzeit_wird_rot():
    """Ein Datum allein genuegt nicht -- der Bericht laeuft zweimal am Tag."""
    block = zd.block(WERTE, "2026-10-17", FENSTER)
    befunde = zd.undeklarierte(nachricht(block))
    assert any("nicht als Datum mit Uhrzeit" in b for b in befunde), befunde


def test_kopf_ohne_quelle_wird_rot():
    block = zd.block(WERTE, STICHTAG, FENSTER)
    ohne = block.replace(f", Quelle {zd.QUELLE}", "")
    befunde = zd.undeklarierte(nachricht(ohne))
    assert any("Quelle" in b for b in befunde), befunde


def test_abweichende_definition_wird_rot():
    """Eine umformulierte Definition im Bericht ist ein zweiter Kanon."""
    block = zd.block(WERTE, STICHTAG, FENSTER)
    umformuliert = block.replace(
        zd.DEKLARATIONEN["test"].definition, "unsere Testagenten")
    befunde = zd.undeklarierte(nachricht(umformuliert))
    assert any("weicht vom Kanon ab" in b for b in befunde), befunde


def test_fehlendes_registered_wird_rot():
    block = zd.block({k: v for k, v in WERTE.items() if k != "registered"},
                     STICHTAG, FENSTER)
    befunde = zd.undeklarierte(nachricht(block))
    assert any("'registered' nicht" in b for b in befunde), befunde


# --- Der Renderer und der Pruefer sind eine Datei --------------------------

def test_jede_zahl_der_reihenfolge_hat_eine_deklaration():
    assert set(zd.REIHENFOLGE) == set(zd.DEKLARATIONEN)


def test_etiketten_sind_eindeutig():
    etiketten = [d.etikett for d in zd.DEKLARATIONEN.values()]
    assert len(etiketten) == len(set(etiketten))


def test_tagesbericht_rendert_den_block_und_nennt_keine_nackte_zahl():
    """Sperrklinke gegen die Rueckkehr der alten Zeile in daily_stats.sh."""
    with open(os.path.join(ROOT, "scripts", "daily_stats.sh"), encoding="utf-8") as f:
        sh = f.read()
    assert "zahl_block.py" in sh
    assert "$AGENT_BLOCK" in sh
    assert "Agents: $REGISTERED registered" not in sh


# --- Das Pruefskript -------------------------------------------------------

def _check(*args):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "check_zahl_deklaration.py"),
         *args],
        check=False, capture_output=True, text=True, cwd=ROOT)


def test_skript_gruen_bei_sauberer_meldung(tmp_path):
    p = tmp_path / "meldung.txt"
    p.write_text(nachricht(zd.block(WERTE, STICHTAG, FENSTER)), encoding="utf-8")
    r = _check("--datei", str(p), "--kein-alterstest")
    assert r.returncode == 0, r.stdout + r.stderr


def test_skript_rot_bei_alter_fassung(tmp_path):
    p = tmp_path / "meldung.txt"
    p.write_text(ALT, encoding="utf-8")
    r = _check("--datei", str(p), "--kein-alterstest")
    assert r.returncode == 1, r.stdout + r.stderr


def test_skript_nicht_gruen_auf_unlesbar(tmp_path):
    """Eine fehlende Datei beantwortet die Frage nicht, sie verhindert sie."""
    r = _check("--datei", str(tmp_path / "gibtesnicht.txt"), "--kein-alterstest")
    assert r.returncode == 2, r.stdout + r.stderr


def test_skript_nicht_gruen_auf_leer(tmp_path):
    p = tmp_path / "leer.txt"
    p.write_text("\n", encoding="utf-8")
    r = _check("--datei", str(p), "--kein-alterstest")
    assert r.returncode == 2, r.stdout + r.stderr


def test_skript_nicht_gruen_auf_veraltet(tmp_path):
    """Eine Datei aus einem alten Lauf ist kein gepruefter Stand."""
    p = tmp_path / "alt.txt"
    p.write_text(nachricht(zd.block(WERTE, STICHTAG, FENSTER)), encoding="utf-8")
    alt = os.path.getmtime(p) - 14 * 3600
    os.utime(p, (alt, alt))
    r = _check("--datei", str(p))
    assert r.returncode == 2, r.stdout + r.stderr


def test_block_renderer_als_skript(tmp_path):
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "zahl_block.py"),
         "--stichtag", STICHTAG, "--fenster", str(FENSTER),
         "--registered", "602", "--active", "341", "--test", "23",
         "--partner-test", "31", "--new-12h", "+0"],
        check=False, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert zd.undeklarierte(r.stdout + "\nCredentials: 1\n") == []


def test_block_renderer_verweigert_ohne_registered():
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "zahl_block.py"),
         "--stichtag", STICHTAG, "--fenster", str(FENSTER), "--active", "341"],
        check=False, capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2, r.stdout + r.stderr
