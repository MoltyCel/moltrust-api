#!/usr/bin/env python3
"""Vorpruefung eines Aufgabentexts — laeuft vor jedem `task create`.

Ein Aufgabentext ist nach der Anlage unveraenderlich (CLAUDE.md, 03.10.2026):
`taskmarket task update` kennt Reward, Ablauf, Bid- und Pitch-Frist, die
Beschreibung nicht. Der Scan und das Gegenlesen vor der Anlage sind die
einzige Gelegenheit. Dieses Programm macht daraus eine Pruefung, die abbricht,
statt eine Durchsicht, an die man sich erinnern muss.

Rot bricht ab und legt nichts an. Gruen sagt, was geprueft wurde, mit Quelle
je Zahl — eine Vorpruefung, die nur "in Ordnung" sagt, ist eine Behauptung.

Herkunft: alle acht Pruefungen kommen aus Faellen des 11.10.2026, an einem
einzigen Aufgabentext. Keine davon ist gedacht; jede hat an diesem Tag etwas
gekostet oder haette es.

STAND: Geruest. Keine der acht Pruefungen ist umgesetzt — sie werfen
`NichtUmgesetzt`, und `tests/test_runde_vorpruefung.py` fuehrt sie als
`xfail(strict=True)`. Sobald eine Pruefung traegt, schlaegt ihr strict-xfail
um und zwingt dazu, die Marke zu entfernen. Ein offener Punkt, der sich nicht
vergessen laesst.

Nicht im stuendlichen Lauf. Die Invariantenklasse H wird nach dem
Aenderungsstopp (BQ1, bis 12.10. 16:00 UTC) und nach dem Ablauf der Runde 5
(14.10.) eingehaengt, damit die laufende Runde ungestoert bleibt.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

# Die drei Staende eines Befunds. `nicht_geprueft` ist ausdruecklich nicht
# gruen: eine fehlende Messung als Zustimmung zu lesen ist der Rueckfallwert
# in der erlaubenden Richtung, und genau der hat am 11.10. bei
# .checkout_owner die Luecke getragen.
ROT = "rot"
GRUEN = "gruen"
OFFEN = "nicht_geprueft"


class NichtUmgesetzt(NotImplementedError):
    """Die Pruefung existiert als Zusage, noch nicht als Code."""


def befund(kennung: str, stand: str, text: str, belege=None) -> dict:
    return {"id": kennung, "stand": stand, "text": text,
            "belege": list(belege or [])}


# --------------------------------------------------------------------------
# H1 — Keine Zusage ohne Ort
# --------------------------------------------------------------------------
def h1_zusage_ohne_ort(text: str, *, wurzel: pathlib.Path | None = None) -> dict:
    """Jeder Satz, der eine kuenftige Folge nennt, braucht ihren Ort.

    Gesucht werden Zusagen ueber spaeteres Verhalten ("takes no place in later
    rounds", "wird in kuenftigen Runden", "we will record"), und je Zusage die
    Datei oder Tabelle, die sie traegt. Fehlt sie: rot.

    Herkunft 11.10.2026: der Text der Runde 5 sagte "that operator takes no
    place in later rounds", und `config/ausgeschlossene-betreiber.json`
    existierte nicht. Eine Zusage ohne Ort ist ein Nachweis, der von Hand
    nachgetragen wird.
    """
    raise NichtUmgesetzt("H1")


# --------------------------------------------------------------------------
# H2 — Keine Zahl im Text ohne Messung an diesem Tag
# --------------------------------------------------------------------------
def h2_zahl_ohne_messung(text: str, *, messen=None) -> dict:
    """Jede Zahl im Text wird am laufenden System geprueft, mit Quelle.

    Grenzen, Takte, Betraege, Platzzahlen. Je Zahl wird die Stelle
    protokolliert, aus der sie stammt (Datei und Zeile oder Abfrage). Weicht
    sie ab: rot. Fehlt die Messung: `nicht_geprueft`, nicht gruen.

    Herkunft 11.10.2026: der Text sagte "a registered DID gets 60 free calls
    an hour". `FREE_CALLS_PER_HOUR = 60` steht in app/free_tier.py, gilt aber
    nur fuer die acht Endpunkte in `FREE_TIER_ENDPOINTS` — von denen keiner im
    Aufgabentext vorkam. Und app/main.py kehrt bei `cost == 0` zurueck, bevor
    der Free-Tier-Code laeuft. Gemessen an einer frischen DID: Guthaben 0 vor
    und nach fuenf Aufrufen, `free_tier_state` 0 Zeilen. Die Zusage war in
    zwei Richtungen falsch und stand fast in einem unveraenderlichen Text.
    """
    raise NichtUmgesetzt("H2")


# --------------------------------------------------------------------------
# H3 — Keine Aussage ueber unser eigenes frueheres Verhalten ohne Belegstelle
# --------------------------------------------------------------------------
def h3_selbstaussage_ohne_beleg(text: str, *, beschreibungen=None) -> dict:
    """Jede Aussage ueber eine fruehere Runde wird gegen deren Text geprueft.

    Nennt der Entwurf eine Runde ("in round 4 we ..."), wird die
    Beschreibung dieser Runde geholt und die Aussage dagegen gehalten. Keine
    Belegstelle: rot.

    Herkunft 11.10.2026: der Entwurf sagte "In round 4 we rejected on a
    criterion submitters could not see in advance and then could not tell them
    why." Gemessen: alle 42 tragen `platz_vergeben`, und die Beschreibung der
    Runde 4 sagt woertlich "The first ten submissions that satisfy every
    condition, by submission time. Submissions past the tenth are qualified
    and unpaid." Das Kriterium stand im Text; gefehlt hat der Kanal. Ein
    oeffentlicher Text ueber unser eigenes Verhalten wird nicht geschaetzt.
    """
    raise NichtUmgesetzt("H3")


# --------------------------------------------------------------------------
# H4 — Keine Folge, die wir nicht einloesen koennen
# --------------------------------------------------------------------------
def h4_folge_ohne_deckung(text: str) -> dict:
    """Eine Drohung, die nach einer Zahlung auf der Kette greifen soll, ist rot.

    Eine Zahlung auf Base kommt nicht zurueck. Ein Satz, der eine Folge
    "danach" ankuendigt, muss sagen, was tatsaechlich geschieht — Vermerk in
    der Ergebnisdatei, Ausschluss fuer kuenftige Runden — und nicht, was wir
    gern taeten.

    Herkunft 11.10.2026: der Entwurf sagte "If a group turns out after the
    fact to have been one operator, its places go." Nach der Auszahlung ist
    das nicht einloesbar. Ersetzt durch die Trennung vor und nach der
    Abrechnung.
    """
    raise NichtUmgesetzt("H4")


# --------------------------------------------------------------------------
# H5 — Jede Frist steht im Text und als Feld
# --------------------------------------------------------------------------
def h5_frist_fehlt(text: str, *, felder=None) -> dict:
    """Einreichungsfrist und Reihungsregel, im Text und als Feld.

    Fehlt eine von beiden: rot. Eine Reihung ohne Frist laesst offen, ob eine
    frueh fertige, spaet einreichende Gruppe eine verdraengt, die alles frueh
    erledigt hat.

    Herkunft 11.10.2026: der Entwurf reihte nach der sechsten Endorsierung
    und verlangte drei Einreichungen, ohne eine Einreichungsfrist zu nennen.
    """
    raise NichtUmgesetzt("H5")


# --------------------------------------------------------------------------
# H6 — Kein Beispielwert in der Vergangenheit
# --------------------------------------------------------------------------
def h6_beispiel_in_der_vergangenheit(text: str, *, jetzt=None) -> dict:
    """Zeitstempel in Vorlagen sind Platzhalter, keine Daten.

    Jeder ISO-8601-Wert im Text wird gegen den Tag der Pruefung gehalten.
    Liegt er in der Vergangenheit: rot. Wer ihn abschreibt, reicht einen
    alten Nachweis ein.

    Herkunft 11.10.2026: in der Vorlage stand
    "timestamp": "2026-10-10T12:00:00Z", einen Tag alt.
    """
    raise NichtUmgesetzt("H6")


# --------------------------------------------------------------------------
# H7 — Jede Absageklasse laesst sich auf eine Bedingung im Text abbilden
# --------------------------------------------------------------------------
def h7_absageklasse_ohne_bedingung(text: str, *, klassen=None) -> dict:
    """Jede Klasse, die die Pruefung vergeben kann, steht im Text.

    Eine Klasse ohne Entsprechung ist eine Absage, die der Einreicher nicht
    vorhersehen konnte — und das ist der Fall, der in Runde 4 nicht
    zustellbar war. Umgekehrt ist eine Bedingung im Text ohne Klasse eine
    Absage ohne Namen.

    Herkunft 11.10.2026: die acht Grundklassen der Gruppenpruefung wurden von
    Hand gegen den Text gehalten. Von Hand heisst: beim naechsten Mal nicht.
    """
    raise NichtUmgesetzt("H7")


# --------------------------------------------------------------------------
# H8 — Jede Zahl in mehr als einer Ausgabe kommt aus einer Herleitung
# --------------------------------------------------------------------------
def h8_zwei_herleitungen(*, modul=None) -> dict:
    """AST-Pruefung: zwei Ausgaben, eine Rechnung.

    Erscheint eine Zahl in result.json und in der Markdown-Auswertung oder in
    einer Meldung, muss sie aus derselben Funktion kommen. Zwei Herleitungen
    weichen ab, und zwar still.

    Herkunft 11.10.2026: result.json sagte "bezahlt 0", die Markdown- und
    Telegram-Zeile desselben Laufs "1 bezahlt, 0,500425 USDC". Nach dem ersten
    Fix sagte Markdown 14 von 15 Abweisungen und die Datei 15. Die falsche
    Zahl ging zweimal auf den Geldkanal.
    """
    raise NichtUmgesetzt("H8")


PRUEFUNGEN = (
    ("H1", h1_zusage_ohne_ort),
    ("H2", h2_zahl_ohne_messung),
    ("H3", h3_selbstaussage_ohne_beleg),
    ("H4", h4_folge_ohne_deckung),
    ("H5", h5_frist_fehlt),
    ("H6", h6_beispiel_in_der_vergangenheit),
    ("H7", h7_absageklasse_ohne_bedingung),
    ("H8", h8_zwei_herleitungen),
)


def pruefe(text: str, **kw) -> list:
    """Alle acht, in der Reihenfolge der Kennungen.

    Eine nicht umgesetzte Pruefung gibt `nicht_geprueft` zurueck und nicht
    gruen. Der Aufrufer entscheidet, ob er darauf anlegt — `main()` tut es
    nicht.
    """
    raus = []
    for kennung, fn in PRUEFUNGEN:
        try:
            if kennung == "H8":
                raus.append(fn(modul=kw.get("modul")))
            else:
                raus.append(fn(text, **{k: v for k, v in kw.items()
                                        if k in fn.__code__.co_varnames}))
        except NichtUmgesetzt:
            raus.append(befund(kennung, OFFEN,
                               f"{kennung} ist zugesagt und nicht umgesetzt"))
    return raus


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="runde_vorpruefung",
        description="Einen Aufgabentext pruefen, bevor er unveraenderlich "
                    "wird. Legt nichts an.")
    ap.add_argument("text", help="Pfad des Aufgabentexts")
    ap.add_argument("--runde", default=None, help="Runden-Kennung")
    a = ap.parse_args(argv)

    pfad = pathlib.Path(a.text)
    if not pfad.is_file():
        print(f"{pfad} ist keine Datei — nichts geprueft, nichts angelegt")
        return 2
    befunde = pruefe(pfad.read_text(encoding="utf-8"), runde=a.runde)

    for b in befunde:
        print(f"  {b['stand']:<14} {b['id']}  {b['text']}")
        for z in b["belege"]:
            print(f"                    {z}")

    rot = [b for b in befunde if b["stand"] == ROT]
    offen = [b for b in befunde if b["stand"] == OFFEN]
    print(f"\n  {len(befunde) - len(rot) - len(offen)} gruen, {len(rot)} rot, "
          f"{len(offen)} nicht geprueft von {len(befunde)}")
    if rot:
        print("  ROT — es wird nichts angelegt.")
        return 1
    if offen:
        # Nicht gruen. Solange das Geruest steht, ist das der Normalfall, und
        # ein Exitcode 0 hier waere die Zustimmung, die es nicht gibt.
        print("  NICHT VOLLSTAENDIG GEPRUEFT — es wird nichts angelegt.")
        return 1
    print("  gruen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
