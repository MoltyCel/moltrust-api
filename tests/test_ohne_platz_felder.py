"""Kein Pflichtfeld in runde4-ohne-platz.json bleibt leer.

Am 09.10.2026 habe ich den Zeitschluessel geraten: `r.get("ts")`. Die
kept-Zeilen tragen `addr`, `addr_lc`, `at`, `did`, `ref` — `ts` ist keiner
davon, also kam `eingereicht: null` heraus, 42-mal. Aufgefallen ist es beim
Nachsehen, nicht beim Schreiben; ohne das waere es erst dem Leser der Datei
aufgefallen, und der ist in diesem Fall ein fremder Agent.

`.get()` gibt bei einem falschen Namen None zurueck und sagt nichts. Genau
deshalb braucht die Ausgabe eine Pruefung: ein geratener Name ist von einem
richtigen nur am Ergebnis zu unterscheiden.

Zwei Tests also. Der erste nimmt Zeilen in der echten Form und prueft die
Ausgabe. Der zweite liest mit dem AST, welche Schluessel die Funktion
ueberhaupt anfasst, und vergleicht sie mit der Form — der faellt auch dann,
wenn eine neue Zeile einen neuen Namen erfindet.
"""
import ast
import importlib.util
import json
import pathlib
import types

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
QUELLE = WURZEL / "scripts" / "runde4_auswertung.py"

# Die Form einer qualifizierten Zeile, nachgesehen am 09.10.2026 an der echten
# Pipeline: `sorted(kept[0])` ergab genau diese fuenf.
ZEILENFORM = {"addr", "addr_lc", "at", "did", "ref"}

# Was in der Ausgabe nicht leer sein darf. `grund` ist konstant, steht aber
# mit drin: eine leere Begruendung waere schlimmer als keine Datei.
PFLICHT = ("aufgabe", "task", "adresse", "did", "eingereicht", "einreichung",
           "grund")


def _modul():
    """Die Quelle laden — ohne den Bytecode-Cache.

    `spec_from_file_location` + `exec_module` nimmt die .pyc aus
    `scripts/__pycache__`. Die gilt als frisch, wenn Groesse und mtime der
    Quelle zu den Werten in der .pyc passen — und bei einer Aenderung innerhalb
    derselben Sekunde passen sie. Am 09.10.2026 hat mich das zwei Gegenproben
    gekostet: der zurueckgesetzte Schluessel blieb rot, weil der Cache
    gemessen wurde und nicht die Datei. `importlib.invalidate_caches()` half
    nicht, das raeumt nur die Finder.

    Deshalb wird die Quelle gelesen und compiliert, jedes Mal. Kein Cache
    dazwischen — genau der Fehler, den dieser Test verhindern soll, war sonst
    eine Ebene hoeher wieder drin.
    """
    quelle = QUELLE.read_text(encoding="utf-8")
    m = types.ModuleType("ra_test")
    m.__file__ = str(QUELLE)
    m.__name__ = "ra_test"          # nicht "__main__": sonst laeuft main()
    exec(compile(quelle, str(QUELLE), "exec"), m.__dict__)
    return m


def _zeile(n):
    """Eine qualifizierte Zeile in der echten Form."""
    return {
        "addr": f"0x{n:040x}",
        "addr_lc": f"0x{n:040x}",
        "at": f"2026-10-0{(n % 8) + 1}T10:0{n % 10}:00.000Z",
        "did": f"did:moltrust:{n:016x}",
        "ref": f"SUB-{n:08X}",
    }


def _gathered(slots=2, ueberhang=3):
    """Eine Aufgabe mit `slots` Plaetzen und `ueberhang` Zeilen darueber."""
    spec = {"ref": "TEST-R4", "id": "0x" + "ab" * 32, "slots": slots,
            "profile": "stage1", "round": "r4", "gross": 0.541}
    kept = [_zeile(i) for i in range(slots + ueberhang)]
    return [(spec, {}, kept, kept, 0, {})], {"TEST-R4": (kept, 0)}


def test_kein_pflichtfeld_leer():
    """Die Ausgabe, aus Zeilen in der echten Form."""
    m = _modul()
    gathered, capped = _gathered()
    raus = m.ohne_platz(gathered, capped)
    assert raus, "keine Eintraege erzeugt"
    leer = {}
    for e in raus:
        for feld in PFLICHT:
            if not e.get(feld):
                leer.setdefault(feld, 0)
                leer[feld] += 1
    assert leer == {}, (
        f"leere Pflichtfelder in {len(raus)} Eintraegen: {leer} — "
        f"ein Schluesselname stimmt nicht mit der Zeilenform ueberein")


def test_nur_die_zahl_ueber_den_plaetzen():
    """Der Ueberhang, nicht alles — `kept_c[slots:]`."""
    m = _modul()
    gathered, capped = _gathered(slots=2, ueberhang=3)
    assert len(m.ohne_platz(gathered, capped)) == 3


def test_liest_nur_schluessel_der_zeilenform():
    """Mit dem AST: welche Namen fasst die Funktion an.

    `.get("ts")` haette hier sofort gemeldet, dass `ts` nicht zur Form
    gehoert — statt still None zu liefern. Das ist der Test, der den
    geratenen Namen am Entstehungsort faengt und nicht beim Leser.
    """
    baum = ast.parse(QUELLE.read_text(encoding="utf-8"))
    fn = next((k for k in ast.walk(baum)
               if isinstance(k, ast.FunctionDef) and k.name == "ohne_platz"),
              None)
    assert fn is not None, "ohne_platz() nicht gefunden"

    gelesen = set()
    for k in ast.walk(fn):
        if (isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)
                and k.func.attr == "get" and k.args
                and isinstance(k.args[0], ast.Constant)
                and isinstance(k.args[0].value, str)):
            ziel = k.func.value
            # Nur die Zeilen-Variable, nicht `capped.get(...)`.
            if isinstance(ziel, ast.Name) and ziel.id == "r":
                gelesen.add(k.args[0].value)

    assert gelesen, "liest keine Schluessel aus der Zeile"
    unbekannt = gelesen - ZEILENFORM
    assert unbekannt == set(), (
        f"liest Schluessel, die eine qualifizierte Zeile nicht hat: "
        f"{sorted(unbekannt)}. Die Form ist {sorted(ZEILENFORM)} — "
        f"`.get()` gibt bei einem falschen Namen None zurueck und sagt nichts.")


def test_die_zeilenform_stimmt_noch():
    """Die Gegenrichtung: aendert sich die Pipeline, faellt dieser Test.

    Ohne ihn wuerde ZEILENFORM irgendwann eine Form beschreiben, die es nicht
    mehr gibt, und der Test oben waere eine Behauptung ueber nichts.
    """
    m = _modul()
    gathered, capped = _gathered()
    _spec, _task, _subs, kept, _cut, _reasons = gathered[0]
    assert set(kept[0]) == ZEILENFORM, (
        f"die Testzeile weicht von ZEILENFORM ab: {sorted(set(kept[0]))}")


def test_die_datei_traegt_nur_adresse_und_did(tmp_path):
    """Keine Namen, kein Profil — nur was der Agent selbst eingereicht hat."""
    m = _modul()
    gathered, capped = _gathered()
    ziel = tmp_path / "ohne-platz.json"
    m.schreibe_ohne_platz(m.ohne_platz(gathered, capped), str(ziel))
    doc = json.loads(ziel.read_text(encoding="utf-8"))
    assert doc["anzahl"] == len(doc["eintraege"])
    erlaubt = set(PFLICHT)
    for e in doc["eintraege"]:
        assert set(e) <= erlaubt, f"unerwartetes Feld: {set(e) - erlaubt}"


def test_die_erzeugte_datei_hat_keine_leeren_felder():
    """Die echte Datei, falls sie vorliegt — die Gegenprobe am Ergebnis."""
    import os

    p = pathlib.Path(os.path.expanduser("~/Downloads/runde4-ohne-platz.json"))
    if not p.exists():
        pytest.skip("runde4-ohne-platz.json liegt hier nicht")
    doc = json.loads(p.read_text(encoding="utf-8"))
    leer = [(e.get("adresse"), f) for e in doc["eintraege"]
            for f in PFLICHT if not e.get(f)]
    assert leer == [], f"leere Pflichtfelder: {leer[:5]}"
    assert doc["anzahl"] == len(doc["eintraege"]) == 42
