"""Which message reaches Lars on Telegram, and which is only filed (ops NOTIFY-4, MELD-1).

Lars, 27./28.10.2026: Telegram only when he has to decide or do something. No
status, no done or acceptance notices, no run confirmations, no daily reports
without a step, no measurement reports. Everything else is worked off by the
consoles and recorded, not delivered. Decisions at most once a day, bundled in
ONE message. Allowed to come at once: money, a security incident, the outage of
a live service, a root one-liner to run.

Every sender names its kind of message (`art=`) at the call site. This table
puts each kind into exactly one class:

    HANDELN  Lars must decide or act. Delivered, at most four lines.
             zustellung "sofort": at once (a step with a deadline, or one of
             the four exceptions); "buendel": into the one decision message
             of the day (app/entscheidungen.py).
    ABLAGE   everything else. Not sent; written to ~/reports/<date>.md
             (app/ablage.py).

A message whose first line reads "Lars muss: nichts" is ABLAGE whatever its
kind says: that line is the sender's own statement that there is no step.

The sentence after each entry is the reason. An entry without one does not
load (tests/test_meldungsarten.py). docs/ops/meldungsarten.md is the same table
for reading; the test keeps the two equal.

Messages without `art=` come from outside the repository (a shell script under
~, the LLM gateway) or from a hand run. They are matched against MUSTER on
their first line; what matches nothing is delivered and logged with art "?",
because a message that is lost is worse than one that should have been filed.
tests/test_meldungsarten.py fails on any send call inside the repository that
names no kind, so "?" is never the answer for code that lives here.
"""
from __future__ import annotations

import re
from typing import NamedTuple

HANDELN = "HANDELN"
ABLAGE = "ABLAGE"
KLASSEN = (HANDELN, ABLAGE)

SOFORT = "sofort"
BUENDEL = "buendel"

# The four exceptions of Lars' rule that may come at once (MELD-1 criterion 2).
AUSNAHMEN = ("geld", "sicherheit", "ausfall", "root")

UNBEKANNT = "?"

# Same string as notify.KEIN_HANDGRIFF; kept here so this module has no import
# cycle with notify. tests/test_meldungsarten.py checks that the two agree.
_KEIN_HANDGRIFF = "Lars muss: nichts"


class Art(NamedTuple):
    klasse: str
    grund: str
    zustellung: str = ""   # HANDELN only: SOFORT or BUENDEL
    ausnahme: str = ""     # HANDELN only, optional: one of AUSNAHMEN


def _h(grund: str, *, ausnahme: str = "") -> Art:
    return Art(HANDELN, grund, SOFORT, ausnahme)


def _b(grund: str) -> Art:
    return Art(HANDELN, grund, BUENDEL)


def _a(grund: str) -> Art:
    return Art(ABLAGE, grund)


ARTEN: dict[str, Art] = {
    # -- HANDELN, at once ---------------------------------------------------
    "reply-entwurf": _h("Lars postet die Antwort auf X selbst, der Entwurf verfaellt nach Stunden."),
    "reply-leseliste": _h("Die Leseliste, aus der Lars von Hand antwortet."),
    "reply-rueckfrage": _h("Der Radar fragt Lars nach dem Grund einer Ablehnung; die Antwort laeuft ueber die Nachricht."),
    "moltbook-entwurf": _h("Ein Moltbook-Antwortentwurf mit Senden und Verwerfen; Lars entscheidet in der Nachricht."),
    "linkedin-entwurf": _h("Lars postet den Beitrag auf LinkedIn selbst, die API darf es nicht."),
    "social-entwurf": _h("Ein Beitrag ist nicht automatisch rausgegangen und liegt bei Lars zum Posten."),
    "linkedin-zahlen": _h("Die Zahlen stehen nur in Lars' LinkedIn-Analyse; er traegt sie nach."),
    "zugang-abgelaufen": _h("Ein Zugang laeuft ab oder ist abgelaufen; erneuern kann nur Lars im Browser."),
    "journal-erinnerung": _h("Das Wochenjournal fasst Lars' eigene Chats zusammen; das kann nur er."),
    "milestone-entwurf": _h("Ein Meilenstein-Post geht nicht mehr autonom raus; Lars postet ihn von Hand."),
    "frist-vorwarnung": _h("Eine Frist laeuft binnen 24 Stunden ab und verlangt eine Entscheidung von Lars."),
    "befehl-antwort": _h("Antwort auf einen Befehl oder eine Antwort, die Lars eben geschickt hat; ohne sie weiss er nicht, ob sie gewirkt hat."),
    "threadwatch-stillstand": _h("Der Moltbook-Botschafter steht; Lars bittet einen Console-Tab, ihn anzusehen."),
    "selftest-sammelmeldung": _h("Nur wenn ihre erste Zeile einen Handgriff nennt; sonst ist sie ABLAGE."),
    "tagesbericht": _h("Nur wenn seine erste Zeile einen Handgriff nennt; sonst ist er ABLAGE."),
    "selftest-ausnahme": _h("Ein Befund unter Geld, Sicherheitsvorfall oder Ausfall (agents/supervision.py)."),
    "usdc-poller-ausfall": _h("Zahlungseingaenge werden nicht gutgeschrieben, solange der Poller steht.", ausnahme="ausfall"),
    "endpunkt-ausfall": _h("Ein oeffentlicher Endpunkt antwortet nicht.", ausnahme="ausfall"),
    "llm-gateway-ausfall": _h("Claude antwortet nicht; Status oder Zahlungsstand pruefen kann nur Lars.", ausnahme="ausfall"),
    "fehlerpfad-wache": _h("Ein deklarierter Alarm feuert nicht mehr; ohne ihn bleibt ein Ausfall stumm.", ausnahme="ausfall"),
    "auszahlung-vorschlag": _h("Eine Auszahlung wartet auf Lars' Freigabe; die Antwort laeuft ueber die Nachricht.", ausnahme="geld"),
    "auszahlung-stoerung": _h("Eine Auszahlung ist begonnen und nicht abgeschlossen oder der Bestand reicht nicht; nachladen oder pruefen muss Lars.", ausnahme="geld"),
    "geld-bewegung": _h("Geld ist auf einer verwalteten Wallet eingetroffen und verlangt einen naechsten Schritt.", ausnahme="geld"),
    "testwallet-tx": _h("Pflicht aus CLAUDE.md: jede Transaktion der Testwallet mit Hash, Betrag und Zweck.", ausnahme="geld"),
    "guthaben-niedrig": _h("Ein bezahlter Dienst laeuft leer oder antwortet nicht mehr; aufladen kann nur Lars.", ausnahme="geld"),
    "finanz-ausloeser": _h("Ein Finanz-Ausloeser ist erreicht; der Schritt gehoert Lars.", ausnahme="geld"),
    "rechtsbuendel": _h("Vor der naechsten Einzahlung muss Lars entscheiden, ob die Bedingungen angenommen werden.", ausnahme="geld"),
    "alte-wallet-eingang": _h("Auf der abgeloesten, offengelegten Wallet ist etwas eingegangen.", ausnahme="sicherheit"),
    "integritaet-abweichung": _h("Ein verankerter Datensatz weicht von der Kette ab; das kann ein Eingriff sein.", ausnahme="sicherheit"),
    "sicherheit-handgriff": _h("Der Sicherheitsbericht nennt einen Punkt, den nur Lars entscheiden oder tun kann (SEC-1101).", ausnahme="sicherheit"),
    "root-einzeiler": _h("Ein Befehl braucht sudo oder root; ausfuehren kann nur Lars.", ausnahme="root"),
    "handlauf": _h("Eine Console meldet aus einem Handlauf einen Schritt fuer Lars."),
    "entscheidungs-buendel": _h("Die eine Entscheidungsnachricht des Tages; je Punkt eine Zeile (app/entscheidungen.py)."),
    # -- HANDELN, bundled once a day --------------------------------------------
    "discovery-thread": _b("Ein fremder Thread mit Empfehlung; Lars entscheidet, ob wir antworten."),
    "content-scout-lead": _b("Ein Anknuepfungspunkt in einem fremden Thread; Lars entscheidet, ob wir antworten."),
    "inhalt-entscheidung": _b("Ein Beitrag haengt fest (zurueckgestellt, ohne Aufhaenger, nicht im Feed); Lars entscheidet, ob er bleibt."),
    "reseller-antrag": _b("Ein Reseller-Antrag wartet auf Annahme oder Ablehnung."),
    "ambassador-ablesung": _b("Die Ablesung ist die Grundlage fuer Lars' Entscheidung ueber den Botschafter."),
    # -- ABLAGE -------------------------------------------------------------------
    "entwurf-blockiert": _a("Das Voice-Gate hat einen Entwurf gesperrt; gepostet wird nichts."),
    "selftest-befund": _a("Befunde des Selbsttests arbeiten die Consoles ab."),
    "auto-repair": _a("Kandidaten fuer Weiterleitungen; die Reparatur ist Console-Arbeit."),
    "usdc-eingang": _a("Ein Zahlungseingang ist gutgeschrieben; zu tun ist nichts."),
    "watchdog-befund": _a("Stille einer Pipeline oder Rueckkehr in Ordnung; das arbeiten die Consoles ab. Ausfaelle oeffentlicher Endpunkte meldet endpoint_probe."),
    "deploy": _a("Ausgang eines Deploys; ein gescheiterter rollt selbst zurueck und gehoert der Console."),
    "pipeline-fehler": _a("Ein Lauf ist gescheitert, angehalten oder blockiert; das arbeitet die Console ab."),
    "pipeline-bericht": _a("Ein Lauf berichtet, was er getan hat."),
    "scout-bericht": _a("Fundstellen und Zusammenfassungen von Scouts, Listenwachen und Review-Laeufen."),
    "messbericht": _a("Zahlen, Kennzahlen, Wochenberichte."),
    "moltbook-wachhund": _a("Stehender Befund, als Gate-Punkt in Issue #1 gemeldet."),
    "budget-meldung": _a("Ein Agent eines Betreibers naehert sich seinem Ausgabendeckel; den Rest regelt der Deckel."),
    "dsgvo-retention": _a("Retention-Laeufe stehen im Tagesbericht."),
    "beobachtung": _a("Eine Wache hat etwas gesehen, das niemand ausloesen muss (Web-Root-Schreibvorgang, abgewiesene Anfragen, Escrow-Lage)."),
}


# First line of a message without art= -> art. Only for senders outside the
# repository and for hand runs; code in the repository names its art.
MUSTER: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^Claude ist gerade nicht erreichbar"), "llm-gateway-ausfall"),
    (re.compile(r"^x402-Test\b|Testwallet"), "testwallet-tx"),
    (re.compile(r"^MolTrust deploy\b"), "deploy"),
    (re.compile(r"^TEST\b"), "handlauf"),
    (re.compile(r"^Lars muss: (?!nichts)"), "handlauf"),
]


class Einordnung(NamedTuple):
    art: str
    klasse: str
    zustellung: str
    ausnahme: str


def _erste_zeile(text: str) -> str:
    for z in (text or "").splitlines():
        z = re.sub(r"<[^>]+>", "", z).strip()
        if z:
            return z
    return ""


def einordnen(art: str | None, text: str) -> Einordnung:
    """The class of one message.

    `art` comes from the call site; None or unknown falls back to MUSTER. A
    first line "Lars muss: nichts" files the message whatever its art says.
    """
    name = art if art in ARTEN else None
    if name is None:
        erste = _erste_zeile(text)
        for muster, kandidat in MUSTER:
            if muster.search(erste):
                name = kandidat
                break
    if (text or "").split("\n", 1)[0].strip() == _KEIN_HANDGRIFF:
        return Einordnung(name or UNBEKANNT, ABLAGE, "", "")
    if name is None:
        # Fail-safe: deliver what nobody classified, and say so in the log.
        return Einordnung(UNBEKANNT, HANDELN, SOFORT, "")
    a = ARTEN[name]
    return Einordnung(name, a.klasse, a.zustellung, a.ausnahme)


def tabelle() -> list[tuple[str, str, str, str]]:
    """(art, klasse, zustellung or ausnahme, grund), sorted for the doc file."""
    rows = []
    for name, a in ARTEN.items():
        wie = a.zustellung + (f", Ausnahme {a.ausnahme}" if a.ausnahme else "")
        rows.append((name, a.klasse, wie, a.grund))
    return sorted(rows, key=lambda r: (r[1] != HANDELN, r[0]))


def markdown() -> str:
    """docs/ops/meldungsarten.md, generated from ARTEN; the test keeps them equal.

        python -m app.meldungsarten > docs/ops/meldungsarten.md
    """
    zeilen = [
        "# Meldungsarten: HANDELN oder ABLAGE",
        "",
        "Generated from `app/meldungsarten.py` (ops NOTIFY-4 criterion 2, MELD-1",
        "criterion 2). Do not edit by hand: change the table in the module and run",
        "`python -m app.meldungsarten > docs/ops/meldungsarten.md`.",
        "",
        "HANDELN reaches Lars on Telegram, at most four lines; `buendel` goes into",
        "the one decision message of the day (08:00 UTC). ABLAGE is not sent and is",
        "written to `~/reports/<date>.md`. A first line `Lars muss: nichts` makes",
        "any message ABLAGE.",
        "",
        "| Art | Klasse | Zustellung | Grund |",
        "|---|---|---|---|",
    ]
    for name, klasse, wie, grund in tabelle():
        zeilen.append(f"| `{name}` | {klasse} | {wie} | {grund} |")
    zeilen += ["", "Messages without an art (senders outside the repository) are matched",
               "on their first line:", ""]
    for muster, name in MUSTER:
        zeilen.append(f"- `{muster.pattern}` -> `{name}`")
    return "\n".join(zeilen) + "\n"


if __name__ == "__main__":
    print(markdown(), end="")
