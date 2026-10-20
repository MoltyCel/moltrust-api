#!/usr/bin/env python3
"""Druckt den Agent-Block der Tagesmeldung, eine Zahl je Zeile mit Definition.

Ops Z1. Rendert, was `scripts/check_zahl_deklaration.py` hinterher prueft, aus
derselben Datei (`app/zahl_deklaration.py`). Zwei Vorlagen waeren der Defekt,
nicht die Absicherung dagegen.

Aufruf aus `scripts/daily_stats.sh`. Fehlt ein Wert, bleibt seine Zeile weg --
eine leere Zahl liest sich als eine echte Null.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import zahl_deklaration


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stichtag", required=True,
                   help="Lesezeitpunkt der Abfrage, Form 2026-10-17 07:00 UTC")
    p.add_argument("--fenster", required=True,
                   help="gemessene Breite des Aktivitaetsfensters in Tagen")
    p.add_argument("--registered")
    p.add_argument("--active")
    p.add_argument("--test")
    p.add_argument("--partner-test", dest="partner_test")
    p.add_argument("--new-12h", dest="new_12h")
    p.add_argument("--funnel-7d", dest="funnel_7d")
    args = p.parse_args()

    werte = {name: getattr(args, name)
             for name in zahl_deklaration.REIHENFOLGE
             if getattr(args, name, None) not in (None, "")}
    if "registered" not in werte:
        print("zahl_block: registered fehlt", file=sys.stderr)
        return 2

    print(zahl_deklaration.block(werte, args.stichtag, args.fenster))
    return 0


if __name__ == "__main__":
    sys.exit(main())
