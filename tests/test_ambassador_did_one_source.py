"""Die Ambassador-DID steht an einer Stelle, und die Zählung folgt ihr.

Das Abschaltkriterium zum 01.11.2026 hängt an der DID des Ambassadors. Wird
sie neu vergeben, schreibt `mark_active` gegen eine Zeile, die es nicht gibt,
und das Ergebnis ist eine Null, die den Namenswechsel misst statt der Arbeit.

`did:moltrust:ambassador0001` kann nicht bleiben, wie es ist: die Form ist
älter als die Konvention, scheitert an `DID_PATTERN` und an §2.2 der
Methodenspezifikation (`did:moltrust:[0-9a-f]{16}`), und nur
`validate_did_lookup` löst sie überhaupt auf. Eine Neuvergabe erzeugt deshalb
eine konforme 16-Hex-DID — eine andere Zeichenkette.

Die Tests halten fest: ein Ort, Umgebung gewinnt, und kein lebendes Modul
trägt die Zeichenkette noch selbst.
"""
import ast
import importlib
import pathlib
import re
import sys

import pytest

AGENTS = pathlib.Path(__file__).resolve().parent.parent / "agents"
LEGACY = "did:moltrust:ambassador0001"


@pytest.fixture()
def activity(monkeypatch):
    """agents/activity.py einzeln laden — die Bots ziehen zu viel mit."""
    sys.path.insert(0, str(AGENTS))
    try:
        mod = importlib.import_module("activity")
        yield importlib.reload(mod)
    finally:
        sys.path.remove(str(AGENTS))


def test_umgebung_gewinnt(activity, monkeypatch):
    """Am Tag der Neuvergabe reicht eine Variable, kein Code-Eingriff."""
    monkeypatch.setenv("AMBASSADOR_DID", "did:moltrust:0123456789abcdef")
    assert activity.ambassador_did() == "did:moltrust:0123456789abcdef"


def test_rueckfall_ist_die_altform(activity, monkeypatch):
    monkeypatch.delenv("AMBASSADOR_DID", raising=False)
    assert activity.ambassador_did() == LEGACY


@pytest.mark.parametrize("wert", ["", "   ", "\t"])
def test_leere_variable_faellt_zurueck(activity, monkeypatch, wert):
    """Eine gesetzte, aber leere Variable darf keine leere DID erzeugen.

    `mark_active("")` würde sonst nur eine Warnung loggen und nichts zählen —
    dieselbe stille Null, nur eine Stufe früher.
    """
    monkeypatch.setenv("AMBASSADOR_DID", wert)
    assert activity.ambassador_did() == LEGACY


def test_die_neuvergebene_form_waere_konform(activity, monkeypatch):
    """Was eine Neuvergabe erzeugt, muss §2.2 bestehen — die Altform tut es nicht."""
    streng = re.compile(r"^did:moltrust:[0-9a-f]{16}$")
    assert not streng.match(LEGACY), "die Altform ist absichtlich nicht konform"
    monkeypatch.setenv("AMBASSADOR_DID", "did:moltrust:cad78d76790d4a40")
    assert streng.match(activity.ambassador_did())


def test_kein_lebendes_modul_traegt_die_zeichenkette_selbst():
    """Zwei Kopien einer Kennung sind eine Kopie zu viel.

    Per AST, nicht per Zeilenform: die Kennung steht in `app/main.py` in einem
    Docstring über `validate_did_lookup`, und eine Prüfung, die Zeilen nach
    `#` sortiert, hält einen Docstring für Code. Erlaubt ist sie damit in
    Kommentaren und Docstrings — dort erklärt sie etwas — und verboten als
    Wert, den ein Modul benutzt.

    Ausgenommen bleibt der eine Rückfallwert in `activity.py`.
    `agent/ambassador.py` ist der seit 2026-06-20 abgeschaltete Mac-Zweig und
    zählt nichts; er steht in der Begründung in activity.py.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    treffer = []
    for p in list((root / "agents").rglob("*.py")) + list((root / "app").rglob("*.py")):
        if ".bak" in p.name:
            continue
        try:
            baum = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        # Docstrings einsammeln, damit sie nicht als Wert zählen.
        docs = set()
        for knoten in ast.walk(baum):
            if isinstance(knoten, (ast.Module, ast.ClassDef,
                                   ast.FunctionDef, ast.AsyncFunctionDef)):
                erste = (knoten.body or [None])[0]
                if (isinstance(erste, ast.Expr)
                        and isinstance(erste.value, ast.Constant)
                        and isinstance(erste.value.value, str)):
                    docs.add(id(erste.value))
        for knoten in ast.walk(baum):
            if not (isinstance(knoten, ast.Constant) and isinstance(knoten.value, str)):
                continue
            if LEGACY not in knoten.value or id(knoten) in docs:
                continue
            if p.name == "activity.py":
                continue          # der eine Rückfallwert
            treffer.append(f"{p.relative_to(root)}:{knoten.lineno}")
    assert not treffer, (
        "die Ambassador-DID steht wieder als Wert im Code: " + ", ".join(treffer)
    )
