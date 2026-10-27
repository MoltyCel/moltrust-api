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

STAND (ops G4-1, 27.10.2026): alle acht Pruefungen sind umgesetzt. Each one
returns one of three states, and `nicht_geprueft` is never green: a check
that lacks its input (no description of the round a sentence talks about,
no refusal classes, a number nothing measured) says so instead of passing.
`--runde dauer` supplies the inputs of the standing round from
scripts/dauerrunde.py, so its public text (G4-4) is checked against the
constants the payout uses.

The checks read text; they are patterns, not understanding. Their limits are
named in each docstring under "Grenze", so nobody reads a green as more than
it is. Every pattern drops negations before it searches (CLAUDE.md,
05.10.2026), and every check has a trigger and a silent probe in
tests/test_runde_vorpruefung.py.

Nicht im stuendlichen Lauf: die Pruefung laeuft vor der Anlage eines Texts,
nicht als Invariante.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

# Die drei Staende eines Befunds. `nicht_geprueft` ist ausdruecklich nicht
# gruen: eine fehlende Messung als Zustimmung zu lesen ist der Rueckfallwert
# in der erlaubenden Richtung, und genau der hat am 11.10. bei
# .checkout_owner die Luecke getragen.
ROT = "rot"
GRUEN = "gruen"
OFFEN = "nicht_geprueft"

WURZEL = pathlib.Path(__file__).resolve().parent.parent


def befund(kennung: str, stand: str, text: str, belege=None) -> dict:
    return {"id": kennung, "stand": stand, "text": text,
            "belege": list(belege or [])}


# --------------------------------------------------------------------------
# Shared text helpers
# --------------------------------------------------------------------------

def saetze(text: str) -> list:
    """Sentences: split at . ! ? followed by space, and at line breaks."""
    raus = []
    for zeile in (text or "").splitlines():
        for s in re.split(r"(?<=[.!?])\s+", zeile):
            if s.strip():
                raus.append(s.strip())
    return raus


def absaetze(text: str) -> list:
    """Paragraphs: split at blank lines."""
    return [a for a in re.split(r"\n\s*\n", text or "") if a.strip()]


# A negation, the auxiliary in front of it and what it governs, up to the end
# of the clause. Removed before a pattern searches, so "nothing can be taken
# back" is not a take-back and "we will not" is not a promise.
_VERNEINUNG = re.compile(
    r"\b(?:(?:will|shall|would|can|do|does|did|is|are)\s+)?"
    r"(no|not|never|nothing|none|cannot|can't|won't|without)\b"
    r"[^.;:,!?]*", re.I)


def ohne_verneinung(s: str) -> str:
    return _VERNEINUNG.sub(" ", s)


# --------------------------------------------------------------------------
# H1 — Keine Zusage ohne Ort
# --------------------------------------------------------------------------

_ZUSAGE = [re.compile(p, re.I) for p in (
    r"\b(later|future|subsequent|coming)\s+rounds?\b",
    r"\bwe\s+will\b", r"\bwe'll\b",
    r"\bwill\s+be\s+(recorded|listed|kept|logged|noted|published|stored|"
    r"excluded)\b",
    r"\b(is|are)\s+(recorded|listed|kept|logged|noted|published)\s+(in|at)\b",
    r"\btakes?\s+no\s+(place|part)\b",
    r"\bfrom\s+now\s+on\b",
    r"\bcarr(y|ies)\s+(over|forward)\b", r"\bcarried\s+(over|forward)\b",
)]
# "takes no place" is a promise phrased as a negation; it is searched in the
# raw sentence, everything else in the sentence without negations.
_ZUSAGE_ROH = (_ZUSAGE[5],)

_ORT = re.compile(
    r"https?://[^\s)\]]+"
    r"|(?<![\w/])(?:[\w.<>{}-]+/)+[\w.<>{}-]+\.(?:json|jsonl|md|py|yaml|yml|"
    r"csv|sql|txt)\b"
    r"|(?<![\w/])/rounds/[^\s)>\]]+", re.I)


def orte(text: str) -> list:
    return [m.group(0).rstrip(".,;:") for m in _ORT.finditer(text or "")]


def h1_zusage_ohne_ort(text: str, *, wurzel: pathlib.Path | None = None) -> dict:
    """Jeder Satz, der eine kuenftige Folge nennt, braucht ihren Ort.

    Gesucht werden Zusagen ueber spaeteres Verhalten ("takes no place in later
    rounds", "wird in kuenftigen Runden", "we will record"), und je Zusage die
    Datei oder Tabelle, die sie traegt. Fehlt sie: rot.

    Herkunft 11.10.2026: der Text der Runde 5 sagte "that operator takes no
    place in later rounds", und `config/ausgeschlossene-betreiber.json`
    existierte nicht. Eine Zusage ohne Ort ist ein Nachweis, der von Hand
    nachgetragen wird.

    Rule: a paragraph with a promise must name a place (a URL, a file path or
    a /rounds/ path) in the same paragraph. A repo-relative path is checked
    against `wurzel` (default: this repo) when its first directory exists
    there; a named file that does not exist is red. A path with a
    placeholder (<KW>) and a URL are not fetched here - that is the live
    probe of the job that publishes the text.

    Grenze: the promise patterns are English and a fixed list. A promise in
    other words passes unseen.
    """
    wurzel = pathlib.Path(wurzel) if wurzel else WURZEL
    rot, belege = [], []
    for absatz in absaetze(text):
        zusagen = []
        for s in saetze(absatz):
            bereinigt = ohne_verneinung(s)
            if any(p.search(s) for p in _ZUSAGE_ROH) or any(
                    p.search(bereinigt) for p in _ZUSAGE if p not in _ZUSAGE_ROH):
                zusagen.append(s)
        if not zusagen:
            continue
        gefunden = orte(absatz)
        if not gefunden:
            rot.extend(f"Zusage ohne Ort: {z!r}" for z in zusagen)
            continue
        for ort in gefunden:
            if ort.startswith(("http://", "https://", "/")) or "<" in ort \
                    or "{" in ort:
                belege.append(f"Ort genannt: {ort}")
                continue
            erster = ort.split("/", 1)[0]
            if (wurzel / erster).is_dir():
                if (wurzel / ort).is_file():
                    belege.append(f"Ort existiert: {ort}")
                else:
                    rot.append(f"Ort genannt, aber nicht vorhanden: {ort}")
            else:
                belege.append(f"Ort genannt (nicht in diesem Repo): {ort}")
    if rot:
        return befund("H1", ROT, "Zusage ohne tragenden Ort", rot)
    if belege:
        return befund("H1", GRUEN, "jede Zusage nennt ihren Ort", belege)
    return befund("H1", GRUEN, "keine Zusage im Text")


# --------------------------------------------------------------------------
# H2 — Keine Zahl im Text ohne Messung an diesem Tag
# --------------------------------------------------------------------------

# Spans that carry digits but are not figures the text promises.
_KEINE_ZAHL = re.compile(
    r"`[^`]*`"                                   # code spans
    r"|https?://\S+"                              # URLs
    r"|(?<![\w])(?:[\w.<>{}-]+/)+[\w.<>{}-]*"     # paths
    r"|did:[a-z0-9]+:[\w.:-]+"                    # DIDs
    r"|\b0x[0-9a-fA-F]+\b"                        # hex, addresses
    r"|\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?"  # dates (H6)
    r"|\b\d{1,2}:\d{2}\b"                         # clock times
    r"|/\d+\b"                                    # /24
    r"|\b[A-Za-z]+-?\d+[A-Za-z\d-]*\b"            # W44, ERC-8004, x402, v2
    r"|\bH\d\b", re.I)

_WORTZAHLEN = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
               "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
               "eleven": 11, "twelve": 12}

_ZAHL = re.compile(r"(?<![\w.,])(\d+(?:\.\d+)?)(?![\w.]*\d)"
                   r"(?:\s+([A-Za-z]+))?")
_WORTZAHL = re.compile(r"\b(" + "|".join(_WORTZAHLEN) + r")\s+([A-Za-z]+)",
                       re.I)


def _konstanten(pfad: pathlib.Path, namen) -> dict:
    """Module-level constants by AST, with their line. Read, not imported."""
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    raus = {}
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and len(knoten.targets) == 1 \
                and isinstance(knoten.targets[0], ast.Name) \
                and knoten.targets[0].id in namen:
            wert = knoten.value
            if isinstance(wert, ast.Call) and wert.args:   # frozenset({...})
                wert = wert.args[0]
            raus[knoten.targets[0].id] = (ast.literal_eval(wert),
                                          knoten.lineno)
    return raus


def _freie_aufrufe(text: str, wurzel: pathlib.Path) -> tuple[list, list]:
    """The claim of 11.10.: "N free calls". Measured against app/free_tier.py.

    The allowance is FREE_CALLS_PER_HOUR, and it covers only the endpoints in
    FREE_TIER_ENDPOINTS. A text that promises free calls without naming one of
    them promises something no call in the text receives.
    """
    rot, belege = [], []
    treffer = re.findall(r"(\d+)\s+free\s+calls?", ohne_verneinung(text), re.I)
    if not treffer:
        return rot, belege
    pfad = wurzel / "app" / "free_tier.py"
    k = _konstanten(pfad, {"FREE_CALLS_PER_HOUR", "FREE_TIER_ENDPOINTS"})
    soll, zeile = k["FREE_CALLS_PER_HOUR"]
    endpunkte, ezeile = k["FREE_TIER_ENDPOINTS"]
    pfade = [e.split(" ", 1)[-1].split("{", 1)[0] for e in endpunkte]
    for t in treffer:
        if int(t) != soll:
            rot.append(f"{t} free calls, gemessen {soll} "
                       f"(app/free_tier.py:{zeile})")
            continue
        if not any(p and p in text for p in pfade):
            rot.append(f"{t} free calls gelten nur fuer FREE_TIER_ENDPOINTS "
                       f"(app/free_tier.py:{ezeile}); keiner davon steht im "
                       f"Text")
            continue
        belege.append(f"{t} free calls = app/free_tier.py:{zeile}")
    return rot, belege


def zahlen(text: str, einheiten=frozenset()) -> list:
    """(value, unit, raw) for every figure in the text.

    Digits anywhere; spelled numbers (one .. twelve) only before a unit some
    measurement knows, or "one operator" would be a figure.
    """
    rein = _KEINE_ZAHL.sub(" ", text or "")
    raus = []
    for m in _ZAHL.finditer(rein):
        try:
            raus.append((Decimal(m.group(1)), (m.group(2) or "").lower(),
                         m.group(0).strip()))
        except InvalidOperation:
            continue
    for m in _WORTZAHL.finditer(rein):
        einheit = m.group(2).lower()
        if einheit in einheiten:
            raus.append((Decimal(_WORTZAHLEN[m.group(1).lower()]), einheit,
                         m.group(0)))
    return raus


def h2_zahl_ohne_messung(text: str, *, messen=None,
                         wurzel: pathlib.Path | None = None) -> dict:
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

    `messen` is a list of {"wert": Decimal, "einheit": (words...), "quelle":
    "file:line"} - or a callable returning one. A figure matches an entry of
    the same value whose unit list holds the word after the figure (or that
    names no unit). A figure whose unit some entry knows, at a value no entry
    has, is red: the text and the measurement disagree. A figure no entry
    knows is not measured.

    Grenze: the free-calls claim is the one claim measured without `messen`.
    Clock times, dates, paths, URLs, DIDs and identifiers like W44 are not
    figures here (dates are H6).
    """
    wurzel = pathlib.Path(wurzel) if wurzel else WURZEL
    eintraege = list(messen() if callable(messen) else (messen or []))
    einheiten = {e for x in eintraege for e in (x.get("einheit") or ())}
    rot, belege = _freie_aufrufe(text, wurzel)
    offen = []
    rest = re.sub(r"\d+\s+free\s+calls?", " ", text or "", flags=re.I)
    for wert, einheit, roh in zahlen(rest, einheiten):
        passend = [x for x in eintraege if Decimal(x["wert"]) == wert and (
            not x.get("einheit") or einheit in x["einheit"])]
        if passend:
            belege.append(f"{roh} = {passend[0]['quelle']}")
        elif einheit and einheit in einheiten:
            soll = sorted({str(x["wert"]) for x in eintraege
                           if einheit in (x.get("einheit") or ())})
            rot.append(f"{roh}: gemessen fuer {einheit!r} nur {', '.join(soll)}")
        else:
            offen.append(f"{roh}: keine Messung")
    if rot:
        return befund("H2", ROT, "Zahl weicht von der Messung ab", rot + offen)
    if offen:
        return befund("H2", OFFEN, "Zahl ohne Messung", offen + belege)
    if belege:
        return befund("H2", GRUEN, "jede Zahl mit Quelle", belege)
    return befund("H2", GRUEN, "keine Zahl im Text")


# --------------------------------------------------------------------------
# H3 — Keine Aussage ueber unser eigenes frueheres Verhalten ohne Belegstelle
# --------------------------------------------------------------------------

_RUNDE = re.compile(r"\b(?:round|runde)\s+(\d+)\b", re.I)
_RUNDE_OHNE_NUMMER = re.compile(
    r"\b(previous|earlier|last|past|prior)\s+rounds?\b", re.I)
_FUELLWOERTER = frozenset(
    "the a an and or of in on at to for by from with that this these those "
    "was were is are be been had have has did does do we our us they their "
    "it its as than then there which who what when where every each all any "
    "round runde could would should".split())
BELEG_ANTEIL = Decimal("0.5")


def _stamm(wort: str) -> str:
    w = wort.lower()
    for endung in ("ions", "ion", "ing", "ed", "es", "s"):
        if len(w) > len(endung) + 3 and w.endswith(endung):
            return w[: -len(endung)]
    return w


def _inhalt(text: str) -> set:
    return {_stamm(w) for w in re.findall(r"[A-Za-z]+", text or "")
            if len(w) > 2 and w.lower() not in _FUELLWOERTER}


def h3_selbstaussage_ohne_beleg(text: str, *, beschreibungen=None,
                                runde: str | None = None) -> dict:
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

    `beschreibungen` is {"r4": "<task text of round 4>", ...}. A sentence
    that names round N is held against that description: at least half of
    its content words (stemmed, without filler) must occur there. Fewer:
    red. No description for that round, or an earlier round without a
    number: not checked. The round being checked (`runde`) is skipped.

    Grenze: word overlap finds a sentence that has no footing in the
    description; it does not prove that a sentence with footing says the
    same thing.
    """
    beschreibungen = beschreibungen or {}
    eigene = (runde or "").lower()
    rot, offen, belege = [], [], []
    for s in saetze(text):
        if _RUNDE_OHNE_NUMMER.search(s) and not _RUNDE.search(s):
            offen.append(f"frueheren Runde ohne Nummer: {s!r}")
            continue
        for nummer in sorted(set(_RUNDE.findall(s))):
            schluessel = f"r{nummer}"
            if schluessel == eigene:
                continue
            quelle = beschreibungen.get(schluessel)
            if not quelle:
                offen.append(f"keine Beschreibung fuer {schluessel}: {s!r}")
                continue
            woerter = _inhalt(_RUNDE.sub(" ", s))
            gedeckt = woerter & _inhalt(quelle)
            anteil = (Decimal(len(gedeckt)) / len(woerter)) if woerter else 1
            zeile = (f"{schluessel}: {len(gedeckt)} von {len(woerter)} "
                     f"Inhaltswoertern in der Beschreibung")
            if anteil < BELEG_ANTEIL:
                rot.append(f"{zeile}: {s!r}")
            else:
                belege.append(zeile)
    if rot:
        return befund("H3", ROT, "Aussage ueber eine Runde ohne Belegstelle",
                      rot + offen)
    if offen:
        return befund("H3", OFFEN, "Aussage ueber eine Runde nicht pruefbar",
                      offen + belege)
    if belege:
        return befund("H3", GRUEN, "jede Aussage ueber eine Runde belegt",
                      belege)
    return befund("H3", GRUEN, "keine Aussage ueber eine fruehere Runde")


# --------------------------------------------------------------------------
# H4 — Keine Folge, die wir nicht einloesen koennen
# --------------------------------------------------------------------------

_RUECKNAHME = [re.compile(p, re.I) for p in (
    r"\b(places?|payouts?|payments?|rewards?|shares?)\s+(go|goes|are\s+"
    r"(forfeited|withdrawn|revoked|lost|reclaimed)|is\s+(forfeited|withdrawn|"
    r"revoked|lost|reclaimed))\b",
    r"\b(forfeits?|claw(ed|s)?\s+back|reclaim(ed|s)?|repa(y|id)|pay\s+back|"
    r"taken?\s+back|returns?\s+the\s+(payment|payout|reward))\b",
    r"\blose[s]?\s+(its|their|the)\s+(place|places|payout|reward|share)\b",
)]
_VOR_DER_ZAHLUNG = re.compile(
    r"\bbefore\s+(the\s+)?(settlement|payout|payment|we\s+pay|paying|"
    r"anything\s+is\s+paid)\b", re.I)


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

    Rule: a sentence that takes a place, payout or payment back is red unless
    it binds the consequence to the time before settlement ("before
    settlement", "before the payout"). Negated forms ("nothing can be taken
    back") are dropped before the search: they state the limit, they do not
    threaten.

    Grenze: a take-back in words outside the list passes unseen.
    """
    rot, belege = [], []
    for s in saetze(text):
        bereinigt = ohne_verneinung(s)
        if not any(p.search(bereinigt) for p in _RUECKNAHME):
            continue
        if _VOR_DER_ZAHLUNG.search(s):
            belege.append(f"vor der Abrechnung gebunden: {s!r}")
        else:
            rot.append(f"Ruecknahme ohne 'before settlement': {s!r}")
    if rot:
        return befund("H4", ROT, "Folge, die nach der Zahlung nicht "
                      "einloesbar ist", rot)
    if belege:
        return befund("H4", GRUEN, "jede Folge greift vor der Abrechnung",
                      belege)
    return befund("H4", GRUEN, "keine angekuendigte Ruecknahme")


# --------------------------------------------------------------------------
# H5 — Jede Frist steht im Text und als Feld
# --------------------------------------------------------------------------

_WOCHENTAG = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_REIHUNG = [re.compile(p, re.I) for p in (
    r"\branked\s+by\b", r"\bin\s+order\s+of\b",
    r"\bby\s+(submission|activation|arrival)\s+time\b",
    r"\bthe\s+first\s+(\w+\s+){0,2}(groups?|submissions?|activations?|"
    r"agents?|places?)\b",
)]
_FRIST = [re.compile(p, re.I) for p in (
    r"\b(within|inside)\s+(the\s+)?(\d+|\w+)\s+(hours?|days?)\b",
    r"\bdeadline\b",
    r"\b(by|until|before)\s+\d{4}-\d{2}-\d{2}",
    r"\b(by|until|before|every|each|on)\s+" + _WOCHENTAG + r"\b[^.]*?"
    r"\d{1,2}:\d{2}\s*utc\b",
)]
_FRISTFELDER = ("duration", "deadline", "frist", "expires_at", "stichtag",
                "fenster_tage")


def h5_frist_fehlt(text: str, *, felder=None) -> dict:
    """Einreichungsfrist und Reihungsregel, im Text und als Feld.

    Fehlt eine von beiden: rot. Eine Reihung ohne Frist laesst offen, ob eine
    frueh fertige, spaet einreichende Gruppe eine verdraengt, die alles frueh
    erledigt hat.

    Herkunft 11.10.2026: der Entwurf reihte nach der sechsten Endorsierung
    und verlangte drei Einreichungen, ohne eine Einreichungsfrist zu nennen.

    Rule: a text that ranks (places, "the first three", "in order of") needs
    a deadline in the text and a deadline field (`felder`, one of duration,
    deadline, frist, expires_at, stichtag, fenster_tage). Text without a
    deadline: red. Field missing from `felder`: red. No `felder` given: not
    checked. A `duration` field in hours must match an "N hours" in the
    text. A text that ranks nothing needs neither.

    Grenze: the ranking and deadline patterns are English and a fixed list.
    """
    rein = ohne_verneinung(text or "")
    reihung = [m.group(0) for p in _REIHUNG for m in p.finditer(rein)]
    if not reihung:
        return befund("H5", GRUEN, "keine Reihung im Text")
    frist = [m.group(0) for p in _FRIST for m in p.finditer(rein)]
    belege = [f"Reihung: {r!r}" for r in reihung]
    if not frist:
        return befund("H5", ROT, "Reihung ohne Frist im Text", belege)
    belege += [f"Frist im Text: {f!r}" for f in frist]
    if felder is None:
        return befund("H5", OFFEN, "Fristfeld nicht uebergeben", belege)
    feld = [k for k in _FRISTFELDER if felder.get(k) not in (None, "")]
    if not feld:
        return befund("H5", ROT, "Frist steht nicht als Feld", belege)
    if "duration" in feld:
        stunden = re.findall(r"\b(\d+)\s+hours?\b", rein, re.I)
        if stunden and str(felder["duration"]) not in stunden:
            return befund("H5", ROT, f"Feld duration={felder['duration']}, "
                          f"Text nennt {', '.join(stunden)} Stunden", belege)
    belege += [f"Feld {k}={felder[k]}" for k in feld]
    return befund("H5", GRUEN, "Reihung mit Frist im Text und als Feld",
                  belege)


# --------------------------------------------------------------------------
# H6 — Kein Beispielwert in der Vergangenheit
# --------------------------------------------------------------------------

_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})(T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
                  r"(?:Z|[+-]\d{2}:?\d{2})?)?\b")


def h6_beispiel_in_der_vergangenheit(text: str, *, jetzt=None) -> dict:
    """Zeitstempel in Vorlagen sind Platzhalter, keine Daten.

    Jeder ISO-8601-Wert im Text wird gegen den Tag der Pruefung gehalten.
    Liegt er in der Vergangenheit: rot. Wer ihn abschreibt, reicht einen
    alten Nachweis ein.

    Herkunft 11.10.2026: in der Vorlage stand
    "timestamp": "2026-10-10T12:00:00Z", einen Tag alt.

    A date without time is in the past when its day is before today; a
    timestamp when it is before now. A value that does not parse is red too:
    a broken example is copied as faithfully as an old one.
    """
    jetzt = jetzt or datetime.now(timezone.utc)
    rot, belege = [], []
    for m in _ISO.finditer(text or ""):
        roh = m.group(0)
        try:
            if m.group(2):
                t = datetime.fromisoformat(roh.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                vorbei = t < jetzt
            else:
                vorbei = date.fromisoformat(roh) < jetzt.date()
        except ValueError:
            rot.append(f"{roh}: kein gueltiger Zeitwert")
            continue
        (rot if vorbei else belege).append(
            f"{roh}: {'vor' if vorbei else 'nach'} {jetzt.isoformat()}")
    if rot:
        return befund("H6", ROT, "Zeitwert in der Vergangenheit", rot)
    if belege:
        return befund("H6", GRUEN, "jeder Zeitwert liegt in der Zukunft",
                      belege)
    return befund("H6", GRUEN, "kein Zeitwert im Text")


# --------------------------------------------------------------------------
# H7 — Jede Absageklasse laesst sich auf eine Bedingung im Text abbilden
# --------------------------------------------------------------------------

# How the condition of each group class (scripts/gruppen.py GRUENDE) reads in
# an English task text. A class is matched when every pattern of its list
# occurs.
BEDINGUNG_DER_KLASSE = {
    "gruppe_nicht_drei": [r"\b(three|3)\s+(agents|members|dids)\b"],
    "gruppe_uneinig": [r"\bsame\s+group\b|\bname\s+(the\s+same|each\s+other)"],
    "gruppe_unvollstaendig": [r"\ball\s+three\b[^.]*\bsubmi"],
    "zu_wenige_beitraege": [r"\b(six|6)\b[^.]*\b(pairs?|endorsements)\b"],
    "fenster_ueberschritten": [r"\b24[- ]hours?\b"],
    "betreiber_deckel": [r"\b(more\s+than|at\s+most)\s+(three|3)\b"],
    "betreiber_nicht_unabhaengig": [r"operator_did|same\s+(address|key)"],
    "betreiber_ausgeschlossen": [r"ausgeschlossene-betreiber\.json|"
                                 r"exclusion\s+list|excluded\s+operators?"],
    "platz_vergeben": [r"\bfirst\s+(three|ten|\d+)\b"],
}


def _bedingung(klasse: str, wert) -> list | None:
    if isinstance(wert, dict) and wert.get("im_text"):
        return list(wert["im_text"])
    return BEDINGUNG_DER_KLASSE.get(klasse)


def h7_absageklasse_ohne_bedingung(text: str, *, klassen=None) -> dict:
    """Jede Klasse, die die Pruefung vergeben kann, steht im Text.

    Eine Klasse ohne Entsprechung ist eine Absage, die der Einreicher nicht
    vorhersehen konnte — und das ist der Fall, der in Runde 4 nicht
    zustellbar war. Umgekehrt ist eine Bedingung im Text ohne Klasse eine
    Absage ohne Namen.

    Herkunft 11.10.2026: die acht Grundklassen der Gruppenpruefung wurden von
    Hand gegen den Text gehalten. Von Hand heisst: beim naechsten Mal nicht.

    `klassen` is {klasse: text} for the group classes, whose conditions stand
    in BEDINGUNG_DER_KLASSE, or {klasse: {"text", "im_text": [patterns]}} as
    scripts/dauerrunde.py KLASSEN carries them. A class whose patterns do not
    all occur in the text is red. A class without patterns, or no `klassen`
    at all: not checked.

    Grenze: only the direction class -> text is checked. A condition in the
    text without a class is not found here; it would need the conditions as
    a list of their own.
    """
    if not klassen:
        return befund("H7", OFFEN, "keine Absageklassen uebergeben")
    rot, offen, belege = [], [], []
    for klasse, wert in klassen.items():
        muster = _bedingung(klasse, wert)
        if not muster:
            offen.append(f"{klasse}: keine Bedingung hinterlegt")
            continue
        fehlt = [m for m in muster if not re.search(m, text or "", re.I)]
        if fehlt:
            rot.append(f"{klasse}: Bedingung nicht im Text ({fehlt[0]})")
        else:
            belege.append(f"{klasse}: Bedingung im Text")
    if rot:
        return befund("H7", ROT, "Absageklasse ohne Bedingung im Text",
                      rot + offen)
    if offen:
        return befund("H7", OFFEN, "Absageklasse nicht pruefbar",
                      offen + belege)
    return befund("H7", GRUEN, "jede Absageklasse steht im Text", belege)


# --------------------------------------------------------------------------
# H8 — Jede Zahl in mehr als einer Ausgabe kommt aus einer Herleitung
# --------------------------------------------------------------------------

_AUSGABE = re.compile(r"(ergebnis|result|markdown|meldung|telegram|zeile|"
                      r"bericht|digest|vorschlag)", re.I)
_HERLEITUNG = [
    re.compile(r"\b(\w*(?:auswertung|verteil|berechne|rechne|abrechnung)\w*"
               r"|\w+_alle|qualify|plaetze|rang)\s*\("),
    re.compile(r"\[\s*:\s*[^\]\s][^\]]*\]"),                 # winners[:n]
]
_DEF = re.compile(r"^([ \t]*)def\s+(\w+)\s*\(", re.M)


def funktionen(quelle: str) -> dict:
    """{name: body} by indentation. Line-based, so a sketch with "..." in
    a signature reads as well as a module that parses."""
    treffer = list(_DEF.finditer(quelle))
    raus = {}
    for i, m in enumerate(treffer):
        tiefe = len(m.group(1).expandtabs())
        ende = len(quelle)
        for n in treffer[i + 1:]:
            if len(n.group(1).expandtabs()) <= tiefe:
                ende = n.start()
                break
        rumpf = quelle[m.end():ende]
        # the signature line is not the body
        rumpf = rumpf.split("\n", 1)[1] if "\n" in rumpf else ""
        raus[m.group(2)] = rumpf
    return raus


def h8_zwei_herleitungen(*, modul=None) -> dict:
    """AST-Pruefung: zwei Ausgaben, eine Rechnung.

    Erscheint eine Zahl in result.json und in der Markdown-Auswertung oder in
    einer Meldung, muss sie aus derselben Funktion kommen. Zwei Herleitungen
    weichen ab, und zwar still.

    Herkunft 11.10.2026: result.json sagte "bezahlt 0", die Markdown- und
    Telegram-Zeile desselben Laufs "1 bezahlt, 0,500425 USDC". Nach dem ersten
    Fix sagte Markdown 14 von 15 Abweisungen und die Datei 15. Die falsche
    Zahl ging zweimal auf den Geldkanal.

    `modul` is source text or a path. An output function is one whose name
    says it writes something out (ergebnis, result, markdown, meldung,
    telegram, zeile, bericht, digest, vorschlag). A derivation inside it is a
    call to an evaluation (…auswertung, …verteil…, …rechne…, …abrechnung…,
    …_alle, qualify, plaetze, rang) or a leading slice such as kept[:slots].
    Two output functions with a derivation each are red: each computes what
    the other should read. One shared derivation, read by all outputs, is
    green. No `modul`: not checked.

    Grenze: the check is by name and form, line-based so a sketch with "..."
    reads too. A derivation under another name, or one hidden in a helper
    the output calls, passes unseen.
    """
    if modul is None:
        return befund("H8", OFFEN, "kein Modul uebergeben")
    if isinstance(modul, pathlib.Path) or (
            isinstance(modul, str) and "\n" not in modul
            and modul.endswith(".py")):
        pfad = pathlib.Path(modul)
        if not pfad.is_absolute():
            pfad = WURZEL / pfad
        quelle = pfad.read_text(encoding="utf-8")
        name = str(pfad.relative_to(WURZEL)) if pfad.is_relative_to(WURZEL) \
            else str(pfad)
    else:
        quelle, name = str(modul), "<quelle>"
    ausgaben = {}
    for fn, rumpf in funktionen(quelle).items():
        if not _AUSGABE.search(fn):
            continue
        eigene = sorted({m.group(0).strip() for p in _HERLEITUNG
                         for m in p.finditer(rumpf)})
        if eigene:
            ausgaben[fn] = eigene
    if len(ausgaben) >= 2:
        return befund("H8", ROT, f"{len(ausgaben)} Ausgaben rechnen selbst "
                      f"({name})",
                      [f"{fn}: {', '.join(h)}" for fn, h in
                       sorted(ausgaben.items())])
    belege = [f"{fn}: {', '.join(h)}" for fn, h in ausgaben.items()]
    return befund("H8", GRUEN, f"hoechstens eine Ausgabe rechnet selbst "
                  f"({name})", belege)


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


def eingaben_der_runde(runde: str | None) -> dict:
    """What a round brings to the checks. Only the standing round so far.

    The constants, the deadline, the refusal classes and the module that
    computes the payout come from scripts/dauerrunde.py, so a public text
    that passes here names the figures the payout uses.
    """
    if (runde or "").lower() not in ("dauer", "dauerrunde"):
        return {}
    from scripts import dauerrunde as D
    return {"messen": D.messwerte, "felder": D.felder(),
            "klassen": D.KLASSEN,
            "modul": pathlib.Path(D.__file__)}


def pruefe(text: str, **kw) -> list:
    """Alle acht, in der Reihenfolge der Kennungen.

    Keyword arguments go to the checks that take them (wurzel, messen,
    beschreibungen, runde, felder, jetzt, klassen, modul). `runde="dauer"`
    fills what is missing from scripts/dauerrunde.py.
    """
    eingaben = eingaben_der_runde(kw.get("runde"))
    eingaben.update({k: v for k, v in kw.items() if v is not None})
    raus = []
    for _kennung, fn in PRUEFUNGEN:
        namen = fn.__code__.co_varnames[:fn.__code__.co_argcount
                                        + fn.__code__.co_kwonlyargcount]
        args = {k: v for k, v in eingaben.items() if k in namen}
        if "text" in namen:
            raus.append(fn(text, **args))
        else:
            raus.append(fn(**args))
    return raus


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="runde_vorpruefung",
        description="Einen Aufgabentext pruefen, bevor er unveraenderlich "
                    "wird. Legt nichts an.")
    ap.add_argument("text", help="Pfad des Aufgabentexts")
    ap.add_argument("--runde", default=None,
                    help="Runden-Kennung; 'dauer' nimmt die Eingaben aus "
                         "scripts/dauerrunde.py")
    ap.add_argument("--beschreibung", action="append", default=[],
                    metavar="rN=PFAD",
                    help="Text einer frueheren Runde fuer H3, mehrfach")
    ap.add_argument("--felder", default=None, metavar="PFAD",
                    help="JSON mit den Feldern der Aufgabe fuer H5")
    ap.add_argument("--modul", default=None, metavar="PFAD",
                    help="Auswertungsmodul fuer H8")
    a = ap.parse_args(argv)

    pfad = pathlib.Path(a.text)
    if not pfad.is_file():
        print(f"{pfad} ist keine Datei — nichts geprueft, nichts angelegt")
        return 2
    beschreibungen = {}
    for b in a.beschreibung:
        schluessel, _, quelle = b.partition("=")
        beschreibungen[schluessel.strip().lower()] = pathlib.Path(
            quelle).read_text(encoding="utf-8")
    felder = (json.loads(pathlib.Path(a.felder).read_text(encoding="utf-8"))
              if a.felder else None)
    befunde = pruefe(pfad.read_text(encoding="utf-8"), runde=a.runde,
                     beschreibungen=beschreibungen or None, felder=felder,
                     modul=pathlib.Path(a.modul) if a.modul else None)

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
        # Nicht gruen. Ein Exitcode 0 hier waere die Zustimmung, die es
        # nicht gibt.
        print("  NICHT VOLLSTAENDIG GEPRUEFT — es wird nichts angelegt.")
        return 1
    print("  gruen.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(WURZEL))
    sys.exit(main())
