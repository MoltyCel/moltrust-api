"""Testhilfe: der Sammelmeldung einen Handgriff voranstellen (ops MELD-1).

Seit dem Nachtrag von Lars vom 28.10.2026 stellt `send_digest` nicht mehr zu,
wenn die erste Zeile "Lars muss: nichts" lautet. Jeder Test, der die Zustellung
selbst prueft — Ping, Eingang, Kanal, Textinhalt —, braucht dafuer einen
Handgriff. Nur die erste Zeile wird ersetzt; der Rest des Textes, den diese
Tests pruefen, bleibt unberuehrt.

Dass die Zustellung ohne Handgriff ausbleibt, prueft
`tests/test_meld1_kein_handgriff.py`, nicht diese Hilfe.
"""
HANDGRIFF = "Lars muss: den Entwurf freigeben"


def erzwinge_handgriff(monkeypatch) -> None:
    from scripts import selftest
    echt = selftest.kurzfassung

    def kurz(*a, **k):
        text = echt(*a, **k)
        rest = text.split("\n", 1)[1] if "\n" in text else ""
        return HANDGRIFF + "\n" + rest

    monkeypatch.setattr(selftest, "kurzfassung", kurz)
