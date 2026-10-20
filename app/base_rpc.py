"""Where every Base RPC call goes, in one place.

Four modules each held their own `BASE_RPC = "https://mainnet.base.org"`. Four
copies of an endpoint is four places to change when it moves, and it has to
move: the public endpoint promises nothing and throttles at its own discretion.
The load test for the track-record path measured what that costs — a hundred
concurrent issuances put about two thousand calls on it and **73 of 100 came
back unanswered**.

One environment variable now decides for all of them, with the public endpoint
as the fallback so nothing breaks before the variable is set.

    BASE_RPC=https://rpc.ankr.com/base/<key>

Sizing, for whoever picks the plan. The track-record path needs one
`eth_getTransactionCount` per issuance plus about twenty-two calls the first
time a wallet is seen, cached permanently after that. At a hundred issuances a
day with every wallet new, that is 2,300 calls a day and roughly 70,000 a
month — under one percent of the smallest free tier on offer. The reason to
move is not volume. It is that a free tier states a number and the public
endpoint does not.
"""

from __future__ import annotations

import os

#: The public endpoint. Correct, free, and entitled to refuse at any moment.
#: Named so it can be recognised, never used as a fallback.
PUBLIC_BASE_RPC = "https://mainnet.base.org"


class BaseRpcNotConfigured(RuntimeError):
    """BASE_RPC fehlt oder ist kein https-URL. Kein Rueckfallwert."""


def base_rpc_url() -> str:
    """Der Base-Endpunkt dieses Prozesses. Fehlt er, bricht es ab.

    Bis zum 09.10.2026 stand hier `or PUBLIC_BASE_RPC`. Das ist genau der
    Rueckfallwert in der erlaubenden Richtung, den die Regel vom 05.10.
    verbietet: ein stiller Wechsel auf einen oeffentlichen Knoten verdeckt,
    dass der eigene nicht antwortet, und sieht in jedem anderen Signal gesund
    aus. Wer den oeffentlichen Knoten will, traegt ihn ein — dann steht es in
    der Konfiguration und nicht in einem Default.

    Gelesen bei jedem Aufruf, nicht beim Import: ein Modul zu importieren ist
    keine Benutzung des Endpunkts, und ein Abbruch beim Import traefe jeden
    Testlauf ohne gesetzte Variable.
    """
    v = os.getenv("BASE_RPC", "").strip()
    if not v:
        raise BaseRpcNotConfigured(
            "BASE_RPC ist nicht gesetzt. Kein Rueckfall auf den oeffentlichen "
            "Knoten — er wuerde verdecken, dass der eigene fehlt.")
    if not v.startswith("https://"):
        raise BaseRpcNotConfigured(
            f"BASE_RPC traegt das Schema {v.split(':', 1)[0]!r}, erwartet https.")
    return v


def is_public() -> bool:
    """Zeigt der eingetragene Endpunkt auf den oeffentlichen Knoten.

    Wert, um ihn beim Start zu protokollieren: ein Dienst, der ein Kontingent
    zu haben glaubt und keines hat, ist der Fall, den der Lasttest fand.
    """
    return base_rpc_url() == PUBLIC_BASE_RPC
