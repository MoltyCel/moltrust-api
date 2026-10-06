"""`last_seen` wird dort gepflegt, wo jeder authentifizierte Aufruf vorbeikommt.

Vorher wanderte das Feld nur in acht Handlern, die daran dachten. Ein Agent
konnte einen gueltigen Schluessel halten, jede halbe Stunde anklopfen und
trotzdem in Richtung „inaktiv" altern — `did:moltrust:cad78d76790d4a40` tat
das vom 02.10. bis zum 06.10.2026 mit 4,8 Tagen Rueckstand.

Diese Tests halten drei Eigenschaften fest, die alle drei Bedingung dafuer
sind, dass die Pflege an dieser Stelle stehen darf:

  * sie schreibt nur, wenn der Eintrag alt ist (Drosselung),
  * sie bindet an die DID und nichts anderes,
  * sie wirft nie, weil sie vor der Antwort auf einen Aufruf laeuft.
"""
import asyncio

import pytest

from app.credits import touch_last_seen


class FakeConn:
    """Merkt sich die Aufrufe, statt eine Datenbank zu brauchen."""

    def __init__(self, raise_on_execute: BaseException | None = None):
        self.calls: list[tuple[str, tuple]] = []
        self._raise = raise_on_execute

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if self._raise is not None:
            raise self._raise
        return "UPDATE 1"


def run(coro):
    return asyncio.run(coro)


def test_schreibt_nur_mit_altersbedingung():
    """Ohne die Bedingung wuerde jeder Aufruf eine Zeile anfassen."""
    conn = FakeConn()
    assert run(touch_last_seen(conn, "did:moltrust:abc")) is True
    sql, args = conn.calls[0]
    assert "UPDATE agents SET last_seen = now()" in sql
    assert "last_active_at = now()" in sql
    # Die Drosselung steht in der WHERE-Klausel, nicht im Python-Code: so
    # entscheidet die Datenbank, und zwei gleichzeitige Aufrufe koennen sich
    # nicht gegenseitig ueberholen.
    assert "last_seen IS NULL" in sql
    assert "interval '5 minutes'" in sql
    assert args == ("did:moltrust:abc",)


def test_bindet_an_die_did():
    """Die DID ist Parameter, nicht Textbaustein — sonst waere das injizierbar."""
    conn = FakeConn()
    run(touch_last_seen(conn, "did:moltrust:x'; DROP TABLE agents; --"))
    sql, args = conn.calls[0]
    assert "DROP TABLE" not in sql
    assert args == ("did:moltrust:x'; DROP TABLE agents; --",)
    assert "WHERE did = $1" in sql


@pytest.mark.parametrize("exc", [
    RuntimeError("pool exhausted"),
    asyncio.TimeoutError(),
    Exception("undefined column last_active_at"),
])
def test_wirft_nie(exc):
    """Buchhaltung darf keinen Aufruf scheitern lassen.

    Das laeuft in `credit_middleware`, bevor die Anfrage bedient wird. Ein
    Agent darf keine 500 bekommen, weil ein Zeitstempel nicht geschrieben
    werden konnte.
    """
    conn = FakeConn(raise_on_execute=exc)
    assert run(touch_last_seen(conn, "did:moltrust:abc")) is False


def test_middleware_ruft_es_auf():
    """Die Verdrahtung selbst, sonst ist der Rest unbenutzt.

    Gelesen statt importiert: `app.main` zieht beim Import die halbe
    Anwendung hoch und verlangt Umgebungsvariablen.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "app" / "main.py"
    text = src.read_text(encoding="utf-8")
    i = text.index("caller_did = await resolve_did_from_api_key(conn, api_key)")
    window = text[i:i + 600]
    assert "await touch_last_seen(conn, caller_did)" in window, (
        "touch_last_seen muss unmittelbar nach der DID-Aufloesung in "
        "credit_middleware stehen — das ist die einzige Stelle, an der jeder "
        "Aufruf mit Schluessel vorbeikommt"
    )
    assert "if caller_did:" in window
