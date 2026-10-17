#!/usr/bin/env python3
"""Steht in pool_spend ein Escrow, der laengst abgerechnet ist?

Eine Zeile fuer `f-escrow-abschluss-gebucht`. Liest, schreibt nichts.

    gleich 2 Escrow-Zeilen, keine abgerechnete darunter
    ABWEICHUNG #95 spent — TSK-09GC1Y9D: completed/resolved, abgerechnet …
    UNREADABLE psql: …

f-no-stale-escrow meldet, dass ein Escrow liegt. Diese Pruefung meldet das
Gegenteil: dass einer nicht mehr liegt und die Buchung es nicht weiss. Beides
sieht in der Datenbank gleich aus — `state = 'escrowed'` und ein Datum —, und
nur die zweite Lage ist still: sie macht die erste Meldung unglaubwuerdig,
ohne selbst eine zu erzeugen. Am 16.10.2026 waren drei der fuenf gemeldeten
Zeilen seit dem 9.10. ausgezahlt, und die Invariante zeigte dieselbe Zahl, die
sie auch bei drei liegenden Betraegen gezeigt haette.

Die Beurteilung kommt aus `escrow_reconcile.py` und nicht aus einer zweiten
Abfrage: eine Wache mit eigener Rechnung laeuft gegen den Abschlussweg
auseinander, den sie bewacht.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import escrow_reconcile as er  # noqa: E402 - Pfad steht vor dem Import


def main() -> int:
    try:
        zeilen = er.offene_zeilen()
    except SystemExit as exc:
        print(f"UNREADABLE {exc}")
        return 0

    abweichend, gelesen = [], 0
    for z in zeilen:
        if not z["tx"]:
            # Eine Zeile ohne Escrow-Transaktion ist nicht beurteilbar, und
            # b-pool-spend-has-state ist nicht die Stelle, die das faengt.
            print(f"UNREADABLE #{z['id']} ohne tx_hash")
            return 0
        try:
            task = er.aufgabe_zu(z["tx"])
            receipts = {}
            for a in (task or {}).get("awards") or []:
                h = a.get("settlementTxHash")
                if h and h not in receipts:
                    receipts[h] = er.rpc("eth_getTransactionReceipt", [h])
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - Markt oder Kette
            # SystemExit mit: ein fehlendes BASE_RPC ist keine Abweichung in
            # der Buchhaltung, sondern eine Prüfung, die nicht gelesen hat.
            print(f"UNREADABLE #{z['id']}: {type(exc).__name__}: {exc}")
            return 0
        gelesen += 1
        neu, text = er.befund(z, task, receipts)
        if neu in er.SCHREIBBAR:
            abweichend.append(f"#{z['id']} {neu} — {text}")

    if abweichend:
        print("ABWEICHUNG " + "; ".join(abweichend)[:800])
    else:
        print(f"gleich {gelesen} Escrow-Zeilen, keine abgerechnete darunter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
