#!/usr/bin/env python3
"""Prueft die zuletzt ausgelieferte Tagesmeldung auf Definition und Stichtag.

Ops Z1. Gelesen wird die Fassung, die `scripts/daily_stats.sh` tatsaechlich
abgeschickt hat, nicht die Vorlage im Quelltext: eine Zahl ohne Definition
schadet in der Nachricht, nicht im Repo, und ein Handgriff am Server kann die
Vorlage umgehen.

Exit 0 sauber, 1 Befund, 2 nicht pruefbar. Nicht pruefbar ist nicht gruen --
eine fehlende Datei beantwortet die Frage nicht, sie verhindert die Antwort.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import zahl_deklaration

VORGABE = os.path.expanduser("~/selftest/daily-stats-last.txt")

# Der Bericht laeuft 07:00 und 19:00 UTC. Eine Datei, die aelter als 13 Stunden
# ist, gehoert zu einem ausgefallenen Lauf -- dass der Lauf ausfiel, meldet die
# Wache, aber eine veraltete Datei darf hier nicht als gepruefter Stand gelten.
MAX_ALTER_S = 13 * 3600


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--datei", default=VORGABE,
                   help=f"ausgelieferte Meldung (Vorgabe {VORGABE})")
    p.add_argument("--kein-alterstest", action="store_true",
                   help="nur den Inhalt pruefen, nicht das Alter")
    args = p.parse_args()

    try:
        with open(args.datei, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"nicht pruefbar: {args.datei}: {e}")
        return 2

    if not text.strip():
        print(f"nicht pruefbar: {args.datei} ist leer")
        return 2

    if not args.kein_alterstest:
        alter = time.time() - os.path.getmtime(args.datei)
        if alter > MAX_ALTER_S:
            print(f"nicht pruefbar: {args.datei} ist "
                  f"{alter / 3600:.1f} h alt, Grenze {MAX_ALTER_S / 3600:.0f} h")
            return 2

    befunde = zahl_deklaration.undeklarierte(text)
    if not befunde:
        print("alle Agentenzahlen tragen Definition und Stichtag")
        return 0

    for b in befunde:
        print(f"Befund: {b}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
