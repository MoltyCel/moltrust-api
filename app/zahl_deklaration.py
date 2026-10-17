"""Jede gezeigte Agent-Zahl traegt ihre Definition und ihren Stichtag.

Ops Z1. Der Tagesbericht (`scripts/daily_stats.sh`, Telegram und E-Mail) nannte
vierzehn Tage lang `Agents: 598 registered`. Die Zahl war richtig, das Etikett
unvollstaendig: `registered` zaehlt jede nicht widerrufene DID auf den Buechern,
ohne Ankerbedingung, ohne die Plattform-Positivliste und mit den eigenen Test-
und System-Agents darin. Die Nachweisseite nennt unter demselben Wort eine
engere Groesse (`registered_with_anchor`, 549 am 16.10.2026), und wer die beiden
Zahlen nebeneinander liest, haelt eine Luecke von 49 fuer einen Fehler.

Deshalb kommt die Definition hier aus einer Datei und wird neben die Zahl
gesetzt, nicht in eine Fussnote. Dieselbe Datei prueft hinterher, ob die
ausgelieferte Nachricht sie wirklich getragen hat: `undeklarierte()` liest den
Block zurueck und macht jede Zahl ohne Definition zu einem Befund. Eine neue
Zahl im Block ohne Eintrag hier ist damit auch ein Befund, nicht nur eine
weggelassene Zeile.

Die Zahlen selbst stehen unveraendert in `app/sql/agent_counts.sql`. Diese Datei
aendert keine Zaehlregel, sie schreibt sie an.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Kopfzeile des Blocks. Steht als Marke in der ausgelieferten Nachricht, damit
# die Pruefung den Block findet, ohne die ganze Nachricht zu kennen.
BLOCK_MARKE = "Agents"

# Woher die Zahlen kommen. Eine Quelle, von /stats und vom Tagesbericht gelesen.
QUELLE = "app/sql/agent_counts.sql"

# Die engere Groesse auf der Nachweisseite. Steht im Block als Abgrenzung, weil
# genau diese Verwechslung den Job ausgeloest hat.
NACHWEIS_HINWEIS = (
    "Nicht die Nachweiszahl: registry-proof.json nennt registered_with_anchor "
    "mit Ankerbedingung, Plattform-Positivliste und eigenem Schnitt "
    "(docs/zaehlregel.md)"
)


@dataclass(frozen=True)
class Deklaration:
    etikett: str
    definition: str


# Eine Zeile Definition je Zahl. `{fenster}` wird durch die gemessene
# Fensterbreite ersetzt -- sie waechst mit den Daten (agent_counts.sql) und darf
# deshalb nicht als feste Zahl im Text stehen.
DEKLARATIONEN: dict[str, Deklaration] = {
    "registered": Deklaration(
        "registered",
        "jede DID auf den Buechern, nicht widerrufen; ohne Ankerbedingung, "
        "ohne Plattformliste, eigene Test- und System-Agents eingeschlossen",
    ),
    "active": Deklaration(
        "active",
        "DIDs mit mindestens einem API-Key-authentifizierten Aufruf in den "
        "letzten {fenster} Tagen; Teilmenge von registered",
    ),
    "test": Deklaration(
        "test",
        "eigene Pruefflaeche: Plattform 'test' plus die System-Agents; "
        "Teilmenge von registered",
    ),
    "partner_test": Deklaration(
        "partner-test",
        "echte Partner-Registrierungen, deren Anzeigename test, probe oder "
        "ambassador enthaelt; Teilmenge von registered, nirgends abgezogen",
    ),
    "new_12h": Deklaration(
        "new",
        "nicht widerrufene DIDs, deren Anlage in den letzten 12 Stunden vor "
        "dem Stichtag liegt",
    ),
    "funnel_7d": Deklaration(
        "funnel",
        "nicht widerrufene DIDs, angelegt seit dem Beginn des siebten Tages "
        "vor heute, nach Herkunftsgruppe",
    ),
}

# Reihenfolge im Block. Getrennt von der Zuordnung, damit eine neue Zahl nicht
# an der Stelle landet, an der sie im Quelltext eingetragen wurde.
REIHENFOLGE = ("registered", "active", "test", "partner_test", "new_12h", "funnel_7d")

_ZAHL = re.compile(r"\d")
_ZEILE = re.compile(r"^\s{2}(?P<etikett>[a-z][a-z-]*)\s(?P<rest>.*)$")


def _definition(name: str, fenster: object) -> str:
    return DEKLARATIONEN[name].definition.replace("{fenster}", str(fenster))


def block(werte: dict[str, object], stichtag: str, fenster: object) -> str:
    """Der Agent-Block fuer den Tagesbericht, eine Zahl je Zeile mit Definition.

    `stichtag` ist der Lesezeitpunkt der Abfrage, nicht die Sendezeit: zwischen
    beiden liegen die uebrigen Abfragen des Berichts, und eine Zahl, die eine
    fremde Uhrzeit traegt, ist am Tag danach nicht mehr nachrechenbar.
    """
    zeilen = [f"{BLOCK_MARKE} (Stichtag {stichtag}, Quelle {QUELLE}):"]
    for name in REIHENFOLGE:
        if name not in werte:
            continue
        d = DEKLARATIONEN[name]
        zeilen.append(f"  {d.etikett} {werte[name]} — {_definition(name, fenster)}")
    zeilen.append(f"  {NACHWEIS_HINWEIS}")
    return "\n".join(zeilen)


def undeklarierte(text: str, fenster: object | None = None) -> list[str]:
    """Befunde gegen eine ausgelieferte Nachricht. Leere Liste heisst sauber.

    Geprueft wird der Block, nicht die ganze Nachricht: die uebrigen Zahlen des
    Berichts (Credits, MCP, Zahlungen) sind keine Agentenzahlen und haben eine
    eigene Herkunft. Was hier rot wird: fehlender Block, fehlender Stichtag,
    eine Zahl ohne Definition, eine Zahl ohne Eintrag in DEKLARATIONEN und eine
    Definition, die vom Kanon abweicht.
    """
    befunde: list[str] = []
    zeilen = text.splitlines()

    kopf = None
    for i, z in enumerate(zeilen):
        if z.startswith(f"{BLOCK_MARKE} ("):
            kopf, start = z, i + 1
            break
    if kopf is None:
        return [f"kein Block '{BLOCK_MARKE} (...)' in der Nachricht"]

    if "Stichtag " not in kopf:
        befunde.append(f"Blockkopf ohne Stichtag: {kopf.strip()}")
    elif not re.search(r"Stichtag \d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", kopf):
        befunde.append(f"Stichtag nicht als Datum mit Uhrzeit lesbar: {kopf.strip()}")
    if QUELLE not in kopf:
        befunde.append(f"Blockkopf nennt die Quelle {QUELLE} nicht: {kopf.strip()}")

    nach_etikett = {d.etikett: name for name, d in DEKLARATIONEN.items()}

    for z in zeilen[start:]:
        if not z.startswith("  "):
            break  # Blockende: die naechste Ueberschrift beginnt am Zeilenanfang
        if not _ZAHL.search(z):
            continue  # der Abgrenzungshinweis traegt keine Zahl
        m = _ZEILE.match(z)
        if not m:
            befunde.append(f"Zahl ohne Etikett am Zeilenanfang: {z.strip()}")
            continue
        etikett = m.group("etikett")
        name = nach_etikett.get(etikett)
        if name is None:
            befunde.append(f"Zahl '{etikett}' hat keinen Eintrag in DEKLARATIONEN")
            continue
        if " — " not in m.group("rest"):
            befunde.append(f"Zahl '{etikett}' ohne Definition ausgeliefert")
            continue
        geliefert = m.group("rest").split(" — ", 1)[1].strip()
        if fenster is None:
            erwartet = re.escape(DEKLARATIONEN[name].definition).replace(
                re.escape("{fenster}"), r"\d+")
            if not re.fullmatch(erwartet, geliefert):
                befunde.append(
                    f"Definition von '{etikett}' weicht vom Kanon ab: {geliefert}")
        elif geliefert != _definition(name, fenster):
            befunde.append(
                f"Definition von '{etikett}' weicht vom Kanon ab: {geliefert}")

    gesehen = {
        m.group("etikett")
        for z in zeilen[start:]
        if z.startswith("  ") and (m := _ZEILE.match(z)) and _ZAHL.search(z)
    }
    if "registered" not in gesehen:
        befunde.append("der Block nennt 'registered' nicht")

    return befunde
