#!/usr/bin/env python3
"""Runde 5: die Gruppenpruefung. Reine Funktionen, keine Datenbank.

Runde 4 zahlte je Einreichung. Runde 5 zahlt je Gruppe, und das ist eine
andere Rechnung: `qualify` entscheidet ueber eine Einreichung, hier wird ueber
drei zusammen entschieden. Deshalb liegt es nebenan und nicht in `qualify`.

Eine Gruppe sind drei DIDs, von denen jede beide anderen endorsiert hat —
sechs Endorsements, eines je gerichtetem Paar. Dass das die Bauform ist, ist
gemessen und nicht gewaehlt: am 10.10.2026 am laufenden System geprueft, dass
ein Agent auf dem Weg aus Runde 4 (register-pop -> signup-did, null Kredite)
`POST /skill/interaction-proof` und `POST /skill/endorse` kostenlos bedienen
kann, dass Selbst-Endorsement mit 400 abgewiesen wird, und dass
`GET /skill/endorsements/<did>` ohne Schluessel antwortet. Letzteres ist der
Grund, warum diese Runde keine Absage traegt, die ein Agent nicht selbst
vorher sehen konnte.

Was im Probelauf NICHT trug, und was das fuer die Pruefung heisst:

- `interaction-proof` nimmt beliebige `agent_a`/`agent_b` an, auch zwei
  fremde DIDs (200). Der Nachweis bezeugt also keine Zusammenarbeit und wird
  hier nicht gezaehlt. Gezaehlt wird das Endorsement: es haengt am API-Key des
  Endorsers und verbietet Selbst-Endorsement.
- `operator_did` laesst sich auf eine fremde DID setzen (200). Es ist eine
  Erklaerung, kein Beweis. Eine Kollision schliesst aus; Verschiedenheit
  beweist nichts. Genau so steht es auch im Aufgabentext.

Die Zahlen der Runde haengen zusammen und sind nicht frei gewaehlt: sechs
Beitraege, Dreier-Deckel je Betreiber, drei unabhaengige Betreiber — das ist
genau ein geschlossenes Dreieck. Jedes Mitglied gibt zwei Endorsements, und
zwei liegt unter dem Deckel von drei. Mit nur zwei Betreibern muesste einer
vier der sechs geben, und daran bricht es.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

FENSTER = timedelta(hours=24)
BEITRAEGE = 6            # sechs gerichtete Paare in einem Dreieck
MITGLIEDER = 3
DECKEL_JE_BETREIBER = 3  # hoechstens drei der sechs von einem Betreiber
GRUPPEN_JE_AUFGABE = 3   # 3 x 3 = 9 Plaetze, der Vertrag zahlt hoechstens 10

# Merkmale, die Unabhaengigkeit stuetzen. Keines beweist sie.
#
# Getrennt in zwei Gruppen, und die Trennung ist die Lehre aus Runde 4. Dort
# waren 42 Absagen unzustellbar, weil ein Entwurfsdetail erst nach der
# Auszahlung auffiel. Ein Ausschluss darf nur an etwas haengen, das die Gruppe
# vorher sehen und aendern kann:
#
# - Adresse, Schluessel und Betreiber kennen die drei voneinander. Eine
#   Kollision darin ist ein Ausschluss, und sie konnten ihn vermeiden.
# - Das Netz kennen sie nicht voneinander. Drei unabhaengige Betreiber koennen
#   sich ein /24 teilen, ohne davon zu wissen. Eine Kollision darin wird
#   vermerkt und ausgewiesen, aber sie schliesst nicht aus — sonst waere es
#   genau wieder eine Absage, die niemand vorhersehen konnte.
#
# Der Aufgabentext nennt dazu "drei verschiedene DIDs". Die DID steht in
# keiner der beiden Listen: eine Gruppe ist eine Menge von drei DIDs,
# Verschiedenheit ist also durch den Bau garantiert. Eine Pruefung, die nie
# ausloesen kann, gehoert nicht in eine Liste gemessener Merkmale — sie waere
# eine vierte Zahl, die wie eine Messung aussieht.
MERKMALE_AUSSCHLUSS = ("adresse", "schluessel", "betreiber")
MERKMALE_HINWEIS = ("netz",)
MERKMALE = MERKMALE_AUSSCHLUSS + MERKMALE_HINWEIS


def paare(mitglieder) -> set:
    """Die sechs gerichteten Paare eines Dreiecks."""
    m = sorted(mitglieder)
    return {(a, b) for a in m for b in m if a != b}


def _zeit(wert) -> datetime | None:
    if wert is None:
        return None
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    s = str(wert).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fenster(endorsements, soll: set) -> datetime | None:
    """Frueheste 24-Stunden-Spanne, in der alle `soll`-Paare vorkommen.

    Zurueck kommt der Zeitpunkt des letzten Endorsements dieser Spanne — der
    Augenblick, in dem die Gruppe vollstaendig war, und damit der Rang.

    Nicht "das sechste Endorsement nach Zeit": liegen fuer ein Paar mehrere
    Zeilen vor (die UNIQUE-Bedingung der Tabelle laeuft ueber
    endorser+endorsed+evidence_hash, mehrfaches Endorsieren ist also moeglich),
    dann darf die Gruppe die Auswahl haben, die ins Fenster passt. Wer
    nachendorsiert, soll dadurch nicht herausfallen.
    """
    zeilen = sorted(
        (z, (e["endorser"], e["endorsed"]))
        for e in endorsements
        if (z := _zeit(e.get("issued_at"))) is not None
        and (e["endorser"], e["endorsed"]) in soll
    )
    if not zeilen:
        return None
    for i, (start, _) in enumerate(zeilen):
        drin = {p for z, p in zeilen[i:] if z - start <= FENSTER}
        if drin >= soll:
            # Das letzte Endorsement, das die Menge voll macht.
            gesehen, bis = set(), None
            for z, p in zeilen[i:]:
                if z - start > FENSTER:
                    break
                gesehen.add(p)
                if gesehen >= soll:
                    bis = z
                    break
            return bis
    return None


def betreiber_anteile(endorsements, soll: set, betreiber: dict) -> dict:
    """{betreiber_did: Zahl der von ihm gegebenen Paare} ueber `soll`."""
    je = defaultdict(set)
    for e in endorsements:
        p = (e["endorser"], e["endorsed"])
        if p in soll:
            je[betreiber.get(e["endorser"]) or e["endorser"]].add(p)
    return {b: len(ps) for b, ps in je.items()}


def kollisionen(mitglieder, merkmale: dict, welche=MERKMALE) -> list:
    """Merkmale, in denen sich zwei Mitglieder gleichen.

    `merkmale` ist {did: {merkmal: wert}}. Ein fehlender Wert ist keine
    Kollision — er ist eine fehlende Messung, und die wird nicht als Befund
    verkauft. Sie steht stattdessen in `ungemessen`.
    """
    treffer = []
    for m in welche:
        werte = defaultdict(list)
        for did in sorted(mitglieder):
            w = (merkmale.get(did) or {}).get(m)
            if w in (None, ""):
                continue
            werte[str(w).lower()].append(did)
        for w, dids in sorted(werte.items()):
            if len(dids) > 1:
                treffer.append({"merkmal": m, "wert": w, "dids": sorted(dids)})
    return treffer


def ungemessen(mitglieder, merkmale: dict) -> list:
    """Merkmale, die fuer mindestens ein Mitglied fehlen."""
    fehlt = []
    for m in MERKMALE:
        ohne = [d for d in sorted(mitglieder)
                if (merkmale.get(d) or {}).get(m) in (None, "")]
        if ohne:
            fehlt.append({"merkmal": m, "dids": ohne})
    return fehlt


# Absagegruende als feste Klassen, wie in Runde 4. Ein Agent kann eine Klasse
# auf eine Verzweigung abbilden; Freitext aendert sich und bricht jeden Leser.
GRUENDE = {
    "gruppe_nicht_drei": "Die genannte Gruppe hat nicht genau drei Mitglieder",
    "gruppe_uneinig": "Die Mitglieder haben verschiedene Gruppen genannt",
    "gruppe_unvollstaendig": "Nicht alle drei Mitglieder haben eingereicht",
    "zu_wenige_beitraege": "Weniger als sechs endorsierte Paare",
    "fenster_ueberschritten": "Die sechs Endorsements liegen in keinem "
                              "24-Stunden-Fenster",
    "betreiber_deckel": "Ein Betreiber hat mehr als drei der sechs "
                        "Endorsements gegeben",
    "betreiber_nicht_unabhaengig": "Zwei Mitglieder teilen ein Merkmal",
    "platz_vergeben": "Die Gruppe ist vollstaendig, aber nicht unter den "
                      "ersten drei",
}


def bewerte(mitglieder, eingereicht: set, endorsements, merkmale: dict) -> dict:
    """Eine Gruppe, gegen alle Bedingungen. Der erste Fehlgrund gewinnt.

    Die Reihenfolge ist nicht beliebig: zuerst, was die Gruppe selbst in der
    Hand hat (Groesse, Einigkeit, Vollstaendigkeit), dann die Arbeit (Paare,
    Fenster), dann der Deckel, dann die Unabhaengigkeit. So nennt die Absage
    das Erste, was die Gruppe haette aendern koennen.
    """
    m = frozenset(mitglieder)
    befund = {
        "gruppe": sorted(m),
        "beitraege": 0,
        "abgeschlossen_am": None,
        "betreiber_anteile": {},
        "kollisionen": [],
        "ungemessen": [],
        "vollstaendig": False,
        "grundklasse": None,
        "grund_text": None,
    }

    def nein(klasse):
        befund["grundklasse"] = klasse
        befund["grund_text"] = GRUENDE[klasse]
        return befund

    if len(m) != MITGLIEDER:
        return nein("gruppe_nicht_drei")
    if not m <= eingereicht:
        return nein("gruppe_unvollstaendig")

    soll = paare(m)
    gegeben = {(e["endorser"], e["endorsed"]) for e in endorsements} & soll
    befund["beitraege"] = len(gegeben)
    if len(gegeben) < BEITRAEGE:
        return nein("zu_wenige_beitraege")

    bis = fenster(endorsements, soll)
    befund["abgeschlossen_am"] = bis.isoformat() if bis else None
    if bis is None:
        return nein("fenster_ueberschritten")

    anteile = betreiber_anteile(endorsements, soll,
                                {d: (merkmale.get(d) or {}).get("betreiber")
                                 for d in m})
    befund["betreiber_anteile"] = anteile
    if any(n > DECKEL_JE_BETREIBER for n in anteile.values()):
        return nein("betreiber_deckel")

    befund["ungemessen"] = ungemessen(m, merkmale)
    befund["kollisionen"] = kollisionen(m, merkmale)
    # Nur die Merkmale, die die drei voneinander kennen, schliessen aus. Die
    # Netz-Kollision steht in `kollisionen` und wird ausgewiesen; sie nimmt
    # keinen Platz weg, weil keine Gruppe sie vorher pruefen konnte.
    if kollisionen(m, merkmale, MERKMALE_AUSSCHLUSS):
        return nein("betreiber_nicht_unabhaengig")

    befund["vollstaendig"] = True
    return befund


def gruppen(entries) -> tuple[list, dict]:
    """Aus Einreichungen die genannten Gruppen. (gruppen, gruende_je_did)

    `entries` sind die von `qualify` behaltenen Einreichungen, je mit `did`
    und `gruppe` (die Menge der drei genannten DIDs). Eine Gruppe entsteht
    nur, wenn alle genannten Mitglieder dieselbe Menge nennen — diese
    Einigkeit ist selbst der Beleg, dass sich drei gefunden haben.
    """
    genannt = {}
    for e in entries:
        g = e.get("gruppe")
        if g:
            genannt[e["did"]] = frozenset(g)

    gruende, kandidaten = {}, {}
    for did, g in genannt.items():
        if len(g) != MITGLIEDER or did not in g:
            gruende[did] = "gruppe_nicht_drei"
            continue
        uneinig = [o for o in sorted(g)
                   if o in genannt and genannt[o] != g]
        if uneinig:
            gruende[did] = "gruppe_uneinig"
            continue
        kandidaten[g] = kandidaten.get(g, set()) | {did}
    return sorted(kandidaten, key=lambda g: sorted(g)), gruende


def rang(befunde, grenze=GRUPPEN_JE_AUFGABE) -> tuple[list, list]:
    """(bezahlt, ohne_platz) — vollstaendige Gruppen nach Abschlusszeit.

    Rang ist der Augenblick, in dem die Gruppe voll war, nicht die
    Einreichungszeit: die Arbeit ist das Dreieck, und die Einreichung ist nur
    die Meldung darueber.
    """
    voll = [b for b in befunde if b["vollstaendig"]]
    voll.sort(key=lambda b: (b["abgeschlossen_am"] or "", b["gruppe"]))
    bezahlt, leer = voll[:grenze], []
    for b in voll[grenze:]:
        leer.append({**b, "grundklasse": "platz_vergeben",
                     "grund_text": GRUENDE["platz_vergeben"]})
    return bezahlt, leer
