"""Fehlerklassen des Enforce-SDK.

Alle Fehler fuehren nie zu einem PERMIT. Wer `on_transport_error="raise"` waehlt, bekommt
sie geworfen; wer beim Default `"deny"` bleibt, bekommt statt der Exception ein DENY-Verdikt
mit dem Fehlertext im `reason`.
"""
from __future__ import annotations


class EnforceError(Exception):
    """Basis aller SDK-Fehler."""


class EnforceTransportError(EnforceError):
    """Der Server war nicht erreichbar, hat abgebrochen oder mit einem Fehlerstatus geantwortet.

    Das ist ausdruecklich KEIN Verdikt. Wer diese Exception faengt, muss selbst entscheiden —
    und die einzige sichere Entscheidung ist, die Aktion nicht auszufuehren.
    """


class EnforceProtocolError(EnforceError):
    """Die Antwort war syntaktisch da, aber nicht die erwartete Form.

    Auch ein unbekannter `verdict`-Wert landet hier: was das SDK nicht kennt, behandelt es
    nicht als Erlaubnis.
    """
