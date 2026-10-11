#!/usr/bin/env python3
"""Eine Runde auswerten, wenn die Frist laeuft - und nichts auszahlen.

Die Runde kommt als Argument (`--runde`). Geschrieben wurde das Programm
fuer Runde 4; die Herkunft unten erklaert die Zaehlregeln.

Der Waechter meldete Ablauf erst danach, und `refund-expired` verlangt null
Einreichungen. Drei Tasks mit zusammen 85 Einreichungen waeren also am
09.10.2026 um 10:36 UTC verfallen: Escrow gebunden, kein Verdikt, keine
Gewinnerliste. Dieser Lauf erzeugt die Liste, aus der eine Auszahlung dann mit
einem einzigen Aufruf gemacht werden kann.

Er zahlt nichts. Keine `accept-submissions`, keine Transaktion. Die Freigabe
gehoert Lars.

Zaehlregel wie in PR #641: jede zaehlende Zeile nennt Grundgesamtheit und
Stichtag, in der Form "x von y". Wo eine Zahl aus einer gefilterten Sicht
stammt, steht die ungefilterte daneben.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notify  # noqa: E402
from scripts import gruppen as G  # noqa: E402
from scripts.task_watch import (  # noqa: E402
    ELIGIBLE,
    FEE_BPS,
    LAEUFE,
    RUNDEN,
    TASKS,
    netto_mikro,
    psql,
    cli,
    deliverables,
    parse_iso,
    qualify,
    round_cap,
)

# Die Runden-Kennung kommt als Argument. Sie stand bis zum 10.10.2026 als
# Konstante hier, und damit war das Programm an eine Runde gebunden — Runde 5
# haette eine Kopie gebraucht, und eine Kopie ist eine zweite Rechnung.
ROUND = "r4"          # Vorgabe; `--runde` ueberschreibt sie
def out_pfad(runde: str) -> str:
    """Die Markdown-Auswertung, je Runde. Hiess bis zum 10.10.2026 fest
    runde4-auswertung.md — ein Programm, das die Runde als Argument nimmt,
    darf sie nicht daneben fest eingebaut haben."""
    return os.path.expanduser(f"~/Downloads/{runde}-auswertung.md")


OUT = out_pfad(ROUND)   # Vorgabe; main() nimmt die Runde aus dem Argument

# Our own addresses. A submission from one of these would be us inside our own
# measurement - question 2 of PR #641. Lower case throughout.
OURS = {
    "0xa175d51bfe0170738720daaec627a84d44dc9eb9",  # taskmarket escrow
    "0xd8f5bb747f7459bf3e1cc1ad041e2ca57b946c38",  # test wallet
    "0x380238347e58435f40b4da1f1a045a271d5838f5",  # productive signer
    "0x9068e25d8ea247a24abf9ff42bfa084931b3ba91",  # stale, unreferenced
}


def allocate_bps(winners: list) -> tuple[list, str]:
    """Shares in whole basis points, summing to exactly 10000.

    The settlement contract rejects anything else. With fewer winners than
    slots the advertised "ten winners share this reward equally" cannot hold
    literally - either the present winners share it, or part of the prize has
    no recipient and the call fails. This splits it among those present and
    says so; the remainder goes to the earliest submissions, one point each,
    so the result is deterministic and does not depend on dict order.
    """
    if not winners:
        return [], "keine Gewinner"
    merged: dict = {}
    for w in winners:
        m = merged.setdefault(w["addr_lc"], {"addr": w["addr"], "slots": 0,
                                             "at": w["at"], "dids": []})
        m["slots"] += 1
        m["at"] = min(m["at"], w["at"])
        m["dids"].append(w.get("did") or "-")
    rows = sorted(merged.values(), key=lambda x: (x["at"], x["addr"]))
    total_slots = sum(r["slots"] for r in rows)
    base = 10000 // total_slots
    for r in rows:
        r["bps"] = base * r["slots"]
    rest = 10000 - sum(r["bps"] for r in rows)
    for i in range(rest):
        rows[i % len(rows)]["bps"] += 1
    note = (f"{total_slots} Plaetze teilen 10000 bp, Grundanteil {base} bp, "
            f"Rest {rest} bp an die {min(rest, len(rows))} fruehesten")
    return rows, note


def gather(runde: str | None = None):
    """(gathered, unreadable) — der Markt gelesen und qualifiziert.

    Herausgezogen aus main(), damit es genau eine Quelle fuer die Auswahl gibt.
    Jeder Eintrag ist (spec, task, subs, kept, cut, reasons), in der Reihenfolge
    der Aufgaben aus TASKS.
    """
    runde = runde or ROUND
    pool = [t for t in TASKS if t.get("round") == runde]
    if not pool:
        return [], [f"Keine Tasks mit round={runde} im Register."]

    cache_eligible: dict = {}
    gathered, unreadable = [], []
    for spec in pool:
        task = (cli("task", "get", spec["id"]) or {}).get("data") or {}
        if not task:
            unreadable.append(f"{spec['ref']}: Task nicht abfragbar")
            continue
        subs = (cli("task", "submissions", spec["id"]) or {}).get("data") or []
        if isinstance(subs, dict):
            subs = subs.get("submissions") or []
        if spec["profile"] not in cache_eligible:
            cache_eligible[spec["profile"]] = ELIGIBLE[spec["profile"]]()
        spec = {**spec, "_description": task.get("description") or ""}
        bodies = deliverables(spec["id"], subs)
        kept, cut, reasons = qualify(spec, subs, bodies,
                                     cache_eligible[spec["profile"]])
        gathered.append((spec, task, subs, kept, cut, reasons))
    return gathered, unreadable


# Aufgegangen in result.json. Der Pfad bleibt als Vorgabe von
# schreibe_ohne_platz() stehen, damit ein Aufrufer die Liste einzeln
# herausziehen kann; main() schreibt sie nicht mehr.
OHNE_PLATZ = os.path.expanduser("~/Downloads/runde4-ohne-platz.json")


def ohne_platz(gathered, capped) -> list:
    """Die gueltigen Einreichungen ohne Platz — qualifiziert und doch leer.

    `kept_c[slots:]`: dieselbe Liste, aus der oben die Gewinner genommen
    werden, nur der Rest. Das ist keine Abweisung — wer so endet, hat alles
    richtig gemacht und war der elfte.
    """
    raus = []
    for spec, _task, _subs, kept, _cut, _reasons in gathered:
        kept_c, _cut_round = capped.get(spec["ref"], (kept, 0))
        for r in kept_c[spec["slots"]:]:
            raus.append({
                "aufgabe": spec["ref"],
                "task": spec["id"],
                "adresse": r.get("addr"),
                "did": r.get("did"),
                # `at`, nicht `ts`: die kept-Zeilen tragen addr, addr_lc, at,
                # did, ref. Mit `ts` kam das Feld leer heraus.
                "eingereicht": r.get("at"),
                "einreichung": r.get("ref"),
                "grund": "Platz vergeben (Rundendeckel "
                         f"{spec['slots']} je Aufgabe erreicht)",
            })
    return raus


def schreibe_ohne_platz(eintraege, pfad=None) -> str:
    """Nur Adresse, DID, Zeit, Aufgabe und der Grund. Keine Namen."""
    pfad = pfad or OHNE_PLATZ
    doc = {
        "runde": ROUND,
        "erzeugt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "zweck": "Gueltige Einreichungen ohne Platz. Keine Abweisung — die "
                 "Aufgabe hat zehn Plaetze, und die waren vergeben.",
        "quelle": "scripts/runden_auswertung.py, gather() + round_cap()",
        "anzahl": len(eintraege),
        "eintraege": eintraege,
    }
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.chmod(pfad, 0o600)
    return pfad


# ── Auszahlung und Ergebnis ────────────────────────────────────────────────

def auszahlungen(runde: str, pfad: str | None = None) -> dict:
    """{(aufgabe, adresse_lc): satz} aus runde<N>-auszahlung.jsonl.

    Die Datei entsteht beim Auszahlen und traegt je Zeile Adresse, Betrag und
    den Settlement-Hash des Vertrags. Sie ist die einzige Quelle fuer die
    tx-Hashes: der Markt gibt sie erst nach dem Aufruf her, und die
    Auswertung laeuft davor.

    Mehrere Saetze je Adresse sind normal — beim Auszahlen wird zuerst
    "beginnt" geschrieben, dann je Zeile einer ohne Hash, dann nach dem
    Nachlesen einer mit. Der letzte mit tx gewinnt.
    """
    import json as _json

    pfad = pfad or os.path.expanduser(
        f"~/Downloads/runde{runde.lstrip('r')}-auszahlung.jsonl")
    raus: dict = {}
    if not os.path.exists(pfad):
        return raus
    with open(pfad, encoding="utf-8") as fh:
        for zeile in fh:
            try:
                s = _json.loads(zeile)
            except ValueError:
                continue
            adr = s.get("adresse")
            if not adr or not s.get("tx"):
                continue
            raus[(s.get("aufgabe"), adr.lower())] = s
    return raus


# --- Runde 5: die Gruppenauswertung ------------------------------------
#
# Die Merkmale kommen aus der eigenen Datenbank und nicht aus der Einreichung:
# wer seine eigene Unabhaengigkeit einreicht, reicht eine Behauptung ein. Die
# eine Ausnahme ist `operator_did` — die steht in `agents`, weil der Agent sie
# selbst ueber /agents/<did>/operator erklaert hat, und genau so steht sie im
# Aufgabentext.


def netz24(ip) -> str | None:
    """Das /24 einer IPv4, sonst None.

    Nur das Netz, nicht die Adresse: gespeichert wird in der Datei ohnehin
    nichts davon, und fuer die Frage "zwei Mitglieder aus demselben Netz"
    reicht das Praefix. Eine IPv6 oder ein unbrauchbarer Wert ist eine
    fehlende Messung und kein Befund — `gruppen.ungemessen` fuehrt sie.
    """
    if not ip:
        return None
    teile = str(ip).strip().split(".")
    if len(teile) != 4:
        return None
    try:
        zahlen = [int(x) for x in teile]
    except ValueError:
        return None
    if any(z < 0 or z > 255 for z in zahlen):
        return None
    return f"{zahlen[0]}.{zahlen[1]}.{zahlen[2]}.0/24"


# Die Form, in der eine DID in eine Abfrage darf. Enger als DID_RE zu sein
# bringt nichts, enger als noetig zu bleiben schon: ein Wert dieser Form
# enthaelt kein Anfuehrungszeichen, keinen Strichpunkt und kein Prozent.
_DID_SQL = re.compile(r"^did:moltrust:[0-9a-f]{16}$")


def sql_liste(dids) -> str:
    """Die DIDs als SQL-Literalliste — oder leer, wenn keine uebrig ist.

    Geprueft wird hier und nicht beim Aufrufer. Eine SQL-Sicherheit, die an
    einem Aufrufer haengt, ist eine Zusage ueber alle kuenftigen Aufrufer;
    `qualify` macht dieselbe Pruefung, aber darauf darf sich diese Stelle
    nicht stuetzen.
    """
    gut = sorted({d for d in dids if isinstance(d, str) and _DID_SQL.match(d)})
    return ",".join(f"'{d}'" for d in gut)


def merkmale_fuer(dids) -> dict:
    """{did: {adresse, schluessel, betreiber, netz}} aus `agents`."""
    liste = sql_liste(dids)
    if not liste:
        return {}
    rows = psql(f"""
        SELECT did, lower(wallet_address), lower(public_key_hex),
               lower(operator_did), registration_ip
          FROM agents
         WHERE did IN ({liste})""")  # nosec B608 - die interpolierte Liste kommt aus `sql_liste` und traegt nur Werte, die `_DID_SQL` erfuellt haben; diese Form laesst kein Anfuehrungszeichen zu. Geprueft in tests/test_r5_sql.py mit feindlichen Eingaben.
    raus = {}
    for r in rows:
        did, wallet, key, op, ip = (list(r) + [None] * 5)[:5]
        raus[did] = {"adresse": wallet or None, "schluessel": key or None,
                     "betreiber": op or None, "netz": netz24(ip)}
    return raus


def endorsements_fuer(dids) -> list:
    """Die Endorsements zwischen den genannten DIDs, als Datenzeilen.

    Nur zwischen ihnen: ein Endorsement an jemanden ausserhalb der Gruppe
    zaehlt fuer diese Gruppe nicht, und es aus der Abfrage zu lassen haelt die
    Menge klein genug, dass `gruppen.fenster` sie mehrfach durchlaufen kann.
    """
    liste = sql_liste(dids)
    if not liste:
        return []
    rows = psql(f"""
        SELECT endorser_did, endorsed_did, issued_at
          FROM endorsements
         WHERE endorser_did IN ({liste})
           AND endorsed_did IN ({liste})
         ORDER BY issued_at""")  # nosec B608 - wie merkmale_fuer: die Liste kommt aus `sql_liste` und traegt nur Werte der Form did:moltrust:<16 hex>.
    return [{"endorser": a, "endorsed": b, "issued_at": c}
            for a, b, c in (list(r)[:3] for r in rows) if a and b]


def frist_vorbei(runde: str, jetzt=None) -> bool:
    """Ist die Frist der Runde abgelaufen?

    Aus `LAEUFE[runde]["deadline"]`, also aus derselben Stelle, an der die
    Wache sie liest. Fehlt der Eintrag, gilt die Frist als NICHT vorbei: ein
    Rueckfall, der aus einer fehlenden Angabe folgt, waere der Rueckfallwert
    in der erlaubenden Richtung.
    """
    spez = LAEUFE.get(runde) or {}
    roh = spez.get("deadline")
    if not roh:
        return False
    try:
        bis = datetime.fromisoformat(str(roh))
    except ValueError:
        return False
    if bis.tzinfo is None:
        bis = bis.replace(tzinfo=timezone.utc)
    return (jetzt or datetime.now(timezone.utc)) > bis


def gruppen_auswertung(spec, kept) -> dict:
    """Eine Gruppen-Aufgabe auswerten. Keine Zahlung, nur das Urteil.

    Zurueck kommt alles, was die Datei und die Vorwarnung brauchen: die
    gebildeten Gruppen, ihr Befund, die bezahlten, die ohne Platz, der
    Rueckfall und die Gruende je DID.
    """
    eingereicht = {e["did"] for e in kept}
    kandidaten, gruende = G.gruppen(kept)

    beteiligte = set(eingereicht)
    for g in kandidaten:
        beteiligte |= set(g)
    merkmale = merkmale_fuer(beteiligte)
    endorse = endorsements_fuer(beteiligte)
    ausgeschlossen = G.ausgeschlossene()

    befunde = [G.bewerte(g, eingereicht, endorse, merkmale,
                         ausgeschlossen=ausgeschlossen)
               for g in kandidaten]
    verteilt = G.plaetze(befunde, kept,
                         frist_vorbei=frist_vorbei(spec.get("round") or ""))

    je_did = {e["did"]: e for e in kept}
    bezahlt = []
    for b in verteilt["gruppen"]:
        for did in b["gruppe"]:
            if did in je_did:
                bezahlt.append(je_did[did])
    bezahlt.extend(verteilt["einzeln"])

    return {"ref": spec["ref"], "weg": verteilt["weg"],
            "kandidaten": kandidaten, "befunde": befunde,
            "bezahlt": bezahlt, "gruppen_ohne_platz": verteilt["ohne_platz"],
            "gruende": gruende, "merkmale": merkmale,
            "vollstaendig": sum(1 for b in befunde if b["vollstaendig"])}


def bedingungen(runde: str) -> dict | None:
    """Die Bedingungen der Runde, wie sie in result.json stehen.

    Aus `RUNDEN` und — fuer die Gruppen-Fassung — aus `scripts/gruppen.py`.
    Die Zahlen, die eine Gruppe erfuellen muss, kommen damit aus dem Modul,
    das sie prueft, und nicht aus einer Abschrift daneben. Eine
    veroeffentlichte Zahl, die der durchsetzende Code nicht kennt, ist eine
    Zusage ohne Deckung.
    """
    spez = RUNDEN.get(runde)
    if not spez:
        return None
    doc = dict(spez)
    if spez.get("fassung") == "gruppen":
        doc["gruppe"] = {
            "mitglieder": G.MITGLIEDER,
            "beitraege": G.BEITRAEGE,
            "fenster_stunden": int(G.FENSTER.total_seconds() // 3600),
            "deckel_je_betreiber_in_gruppe": G.DECKEL_JE_BETREIBER,
            "gruppen_je_aufgabe": G.GRUPPEN_JE_AUFGABE,
            "plaetze_je_aufgabe": G.GRUPPEN_JE_AUFGABE * G.MITGLIEDER,
            "merkmale_ausschluss": list(G.MERKMALE_AUSSCHLUSS),
            "merkmale_hinweis": list(G.MERKMALE_HINWEIS),
            # Die Liste gehoert in die veroeffentlichte Datei, weil der
            # Aufgabentext sie zusagt. Ein Ausschluss, den nur wir sehen, ist
            # keiner, den ein Leser nachpruefen kann.
            "ausschlussliste": {
                "pfad": "config/ausgeschlossene-betreiber.json",
                "eintraege": len(G.ausgeschlossene()),
            },
            "grundklassen": dict(sorted(G.GRUENDE.items())),
        }
    return doc


def ergebnis(runde, gathered, capped, zahlungen, stand="offen") -> dict:
    """Das Rundenergebnis, maschinenlesbar. Eine Stelle, drei Gruppen.

    `bezahlt`, `ohne_platz` und `abgewiesen` kommen aus derselben Pipeline wie
    die Markdown-Auswertung; die tx-Hashes aus der Auszahlungsdatei. Keine
    zweite Rechnung: wer hier eine andere Zahl findet als dort, hat einen
    Fehler gefunden und nicht eine zweite Meinung.

    Je Eintrag nur Adresse und DID. Beides hat der Agent selbst eingereicht
    und steht ohnehin auf der Kette; Namen, Profile und Bewertungen gehoeren
    nicht in eine Datei, die ohne Anlass veroeffentlicht wird.
    """
    aufgaben, bezahlt, leer, abgewiesen = [], [], [], []
    gruppen_liste = []
    summe_bezahlt = summe_gebuehr = 0
    einreichungen = 0

    gruppen_befunde = {}
    for spec, task, subs, kept, _cut, reasons in gathered:
        ref = spec["ref"]
        kept_c, cut_round = capped.get(ref, (kept, 0))

        if spec.get("profile") == "gruppe":
            # Runde 5: der Platz gehoert der Gruppe. Die Herleitung steht in
            # `gruppen_auswertung`; hier wird nur eingehaengt, damit Abgleich,
            # Summe und Dokumentenrumpf fuer beide Fassungen dieselben sind.
            aus = gruppen_auswertung(spec, kept_c)
            gruppen_befunde[ref] = aus
            bezahlt_dids = {e["did"] for e in aus["bezahlt"]}
            leer_dids = {d for b in aus["gruppen_ohne_platz"]
                         for d in b["gruppe"]}
            for b in aus["befunde"]:
                stand_g = ("bezahlt" if set(b["gruppe"]) <= bezahlt_dids
                           else "ohne_platz" if set(b["gruppe"]) & leer_dids
                           else "nicht_anerkannt")
                gruppen_liste.append({
                    "aufgabe": ref,
                    "mitglieder": b["gruppe"],
                    "stand": stand_g,
                    "vollstaendig": b["vollstaendig"],
                    "beitraege": b["beitraege"],
                    "abgeschlossen_am": b["abgeschlossen_am"],
                    # Eine vollstaendige Gruppe ohne Platz traegt ihren
                    # Grund. `rang()` setzt ihn auf die Kopien, die es
                    # zurueckgibt; hier wird aus den Befunden gebaut, und
                    # dort stand None. Ein Leser haette `ohne_platz` ohne
                    # Grund gefunden.
                    "grundklasse": (b["grundklasse"] or
                                    ("platz_vergeben"
                                     if stand_g == "ohne_platz" else None)),
                    "grund_text": (b["grund_text"] or
                                   (G.GRUENDE["platz_vergeben"]
                                    if stand_g == "ohne_platz" else None)),
                    # Nur der Merkmalsname, nicht sein Wert: dass zwei
                    # Mitglieder dasselbe Netz haben, ist der Befund; welches,
                    # gehoert nicht in eine veroeffentlichte Datei.
                    "kollisionen": sorted({k["merkmal"]
                                           for k in b["kollisionen"]}),
                    "ungemessen": sorted({u["merkmal"]
                                          for u in b["ungemessen"]}),
                })
            gewinner = aus["bezahlt"][:spec["slots"]]
            ohne = [{"aufgabe": ref, "adresse": e.get("addr"),
                     "did": e.get("did"), "eingereicht": e.get("at"),
                     "grundklasse": "platz_vergeben"}
                    for b in aus["gruppen_ohne_platz"] for did in b["gruppe"]
                    if (e := next((x for x in kept_c if x["did"] == did), None))]
            leer.extend(ohne)
        else:
            gewinner = kept_c[:spec["slots"]]
        rows, _note = allocate_bps(gewinner)
        brutto = int(round(spec["gross"] * 1_000_000))
        gezahlt = gebuehr = 0

        for r in rows:
            s = zahlungen.get((ref, (r["addr"] or "").lower()), {})
            betrag = s.get("betrag_mikro")
            bezahlt.append({
                "aufgabe": ref,
                "adresse": r["addr"],
                "did": (r.get("dids") or [None])[0],
                "bps": r["bps"],
                "betrag_mikro": betrag,
                "tx": s.get("tx"),
                "eingereicht": r.get("at"),
            })
            if betrag:
                gezahlt += int(betrag)
            if s.get("gebuehr_mikro"):
                gebuehr += int(s["gebuehr_mikro"])

        if spec.get("profile") != "gruppe":
            for r in kept_c[spec["slots"]:]:
                leer.append({
                    "aufgabe": ref,
                    "adresse": r.get("addr"),
                    "did": r.get("did"),
                    "eingereicht": r.get("at"),
                    "grundklasse": "platz_vergeben",
                })

        # Abgewiesen: der Qualifizierer zaehlt je Grund, nicht je Adresse.
        # Die Grundklassen stehen hier als Paare (Klasse, Zahl) — eine Liste
        # je Adresse gaebe es nur, wenn `qualify` sie zurueckgaebe, und eine
        # zweite Rechnung dafuer ist genau das, was dieser Auftrag abschafft.
        # Der Rundendeckel ist ein eigener Posten. Er steht in `capped`, nicht
        # in `reasons`: die Markdown-Schleife haengt ihn dort erst spaeter ein,
        # und sie laeuft nach dieser Funktion. Die erste Fassung las `reasons`
        # vorher und kam auf 20 statt 49 Abweisungen — gemeldet hat es der
        # Abgleich in der Datei selbst, mit 29 unter „ohne aufgeschluesselten
        # Grund".
        gruende = dict(reasons)
        if cut_round:
            gruende[f"Adresse hat schon einen Platz in Runde {runde}"] = cut_round
        if spec.get("profile") == "gruppe":
            # Je Gruppe ein Grund, gezaehlt nach den Mitgliedern, die
            # eingereicht haben — sonst traegt der Abgleich die Mitglieder
            # einer gescheiterten Gruppe nirgends, und ein Leser findet
            # Einreichungen ohne Grund.
            aus = gruppen_befunde[ref]
            bezahlt_dids = {e["did"] for e in aus["bezahlt"]}
            leer_dids = {did for b in aus["gruppen_ohne_platz"]
                         for did in b["gruppe"]}
            for b in aus["befunde"]:
                if b["vollstaendig"]:
                    continue
                n = sum(1 for e in kept_c if e["did"] in set(b["gruppe"])
                        and e["did"] not in bezahlt_dids
                        and e["did"] not in leer_dids)
                if n:
                    gruende[b["grund_text"]] = gruende.get(b["grund_text"], 0) + n
            # Wer ueberhaupt keine Gruppe gebildet hat.
            for did, klasse in aus["gruende"].items():
                if did in bezahlt_dids or did in leer_dids:
                    continue
                txt = G.GRUENDE[klasse]
                gruende[txt] = gruende.get(txt, 0) + 1
        for grund, n in sorted(gruende.items()):
            abgewiesen.append({
                "aufgabe": ref,
                "grundklasse": grundklasse(grund),
                "grund_text": grund,
                "anzahl": n,
            })

        einreichungen += len(subs)
        summe_bezahlt += gezahlt
        summe_gebuehr += gebuehr
        aufgaben.append({
            "ref": ref,
            "task": spec["id"],
            "ablauf": task.get("expiryTime"),
            "einreichungen": len(subs),
            "plaetze": spec["slots"],
            "praemie_brutto_mikro": brutto,
            "bezahlt_mikro": gezahlt or None,
            "gebuehr_mikro": gebuehr or None,
        })

    n_abgewiesen = sum(e["anzahl"] for e in abgewiesen)
    bed = bedingungen(runde) or {}
    return {
        "runde": runde,
        "stand": stand,
        "version": 1,
        "ersetzt": None,
        "erzeugt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quelle": "scripts/runden_auswertung.py",
        "bedingungen": bed or None,
        "rundendeckel": {
            # Aus dem Register, wenn der Markt gelesen wurde; sonst aus den
            # Bedingungen. Vor der Runde stand hier {null, null}, waehrend
            # dieselbe Zahl in `bedingungen` schon feststand.
            "plaetze_je_aufgabe": (
                gathered[0][0]["slots"] if gathered
                else ((bed.get("gruppe") or {}).get("plaetze_je_aufgabe")
                      or bed.get("plaetze_je_aufgabe"))),
            "plaetze_je_adresse_je_runde": (
                gathered[0][0].get("cap") if gathered
                else bed.get("plaetze_je_adresse_je_runde")),
        },
        "aufgaben": aufgaben,
        "bezahlt": bezahlt,
        "ohne_platz": leer,
        "abgewiesen": abgewiesen,
        # Nur in der Gruppenfassung. In der Einzelfassung waere eine leere
        # Liste eine Zusage, die es dort nicht gibt.
        **({"gruppen": gruppen_liste} if gruppen_liste else {}),
        "abgleich": {
            "einreichungen": einreichungen,
            "bezahlt": len(bezahlt),
            "ohne_platz": len(leer),
            "abgewiesen": n_abgewiesen,
            "ohne_aufgeschluesselten_grund": (
                einreichungen - len(bezahlt) - len(leer) - n_abgewiesen),
        },
        "summe": {
            "an_arbeiter_mikro": summe_bezahlt or None,
            "gebuehr_mikro": summe_gebuehr or None,
        },
    }


# Freitext der Qualifizierer-Gruende auf eine feste Menge abgebildet. Ein
# Agent kann eine Klasse auf eine Verzweigung abbilden; Freitext aendert sich,
# und dann bricht jeder Leser. Der Originaltext steht als `grund_text` daneben,
# damit die Abbildung nachpruefbar bleibt.
GRUNDKLASSEN = (
    ("hat schon einen Platz in Runde", "adresse_hat_schon_platz_in_runde"),
    ("hat schon einen Platz in dieser Aufgabe", "adresse_hat_schon_platz_in_aufgabe"),
    ("Zweiteinreichung derselben DID", "zweiteinreichung_derselben_did"),
    ("kein lesbares JSON", "kein_lesbares_json"),
    ("Annahmekriterien", "did_erfuellt_annahmekriterien_nicht"),
    ("endpoint_used", "endpoint_used_fehlt"),
)


def grundklasse(text: str) -> str:
    for muster, klasse in GRUNDKLASSEN:
        if muster in text:
            return klasse
    return "sonstiger_grund"


def schreibe_result(doc: dict, pfad: str) -> dict:
    """Die gueltige Fassung bleibt an der festen Adresse.

    Eine Korrektur schiebt die bisherige nach `result-<n>.json` und legt die
    neue wieder als `result.json` ab. Ein Leser findet damit immer die
    gueltige Fassung unter derselben Adresse und die Geschichte ueber
    `ersetzt`.

    `version` steigt bei jeder Aenderung — ein lautloser Wechsel ist damit
    ausgeschlossen. Aendert sich nichts, wird nicht geschrieben und die
    Version bleibt: eine erhoehte Version ohne Aenderung waere dieselbe Luege
    in die andere Richtung.

    `stand` wechselt nur von `offen` auf `ausgezahlt`. Der Rueckweg wird
    abgewiesen, nicht stillschweigend uebernommen.
    """
    import json as _json

    ziel = pathlib.Path(pfad)
    ziel.parent.mkdir(parents=True, exist_ok=True)

    def vergleichbar(d):
        """Alles ausser der Buchfuehrung — daran wird Aenderung gemessen."""
        return {k: v for k, v in d.items()
                if k not in ("version", "ersetzt", "erzeugt")}

    if not ziel.exists():
        doc["version"] = 1
        doc["ersetzt"] = None
        ziel.write_text(_json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        return {"geschrieben": True, "version": 1, "ersetzt": None,
                "grund": "erste Fassung"}

    alt = _json.loads(ziel.read_text(encoding="utf-8"))
    alte_version = int(alt.get("version") or 1)

    if alt.get("stand") == "ausgezahlt" and doc.get("stand") == "offen":
        raise ValueError(
            "stand wechselt nur von offen auf ausgezahlt, nicht zurueck "
            f"(vorhanden: ausgezahlt, neu: offen) in {pfad}")

    if vergleichbar(alt) == vergleichbar(doc):
        return {"geschrieben": False, "version": alte_version,
                "ersetzt": alt.get("ersetzt"),
                "grund": "unveraendert, Version bleibt"}

    vorgaenger = ziel.with_name(f"result-{alte_version}.json")
    if vorgaenger.exists():
        raise ValueError(f"{vorgaenger} existiert schon — die Geschichte "
                         f"waere ueberschrieben")
    vorgaenger.write_text(_json.dumps(alt, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")
    doc["version"] = alte_version + 1
    doc["ersetzt"] = vorgaenger.name
    ziel.write_text(_json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return {"geschrieben": True, "version": doc["version"],
            "ersetzt": vorgaenger.name,
            "grund": f"geaendert gegenueber Version {alte_version}"}


WEB = pathlib.Path("/home/moltstack/moltrust-web")


def result_pfad(runde: str, wurzel=None) -> pathlib.Path:
    """Die feste Adresse: <wurzel>/rounds/<runde>/result.json.

    Im moltrust-web-Checkout, weil das Ergebnis statisch ausgeliefert wird.
    Eine Route wuerde dazu verfuehren, es bei jedem Abruf neu zu rechnen; ein
    Rundenergebnis ist unveraenderlich.
    """
    return pathlib.Path(wurzel or WEB) / "rounds" / runde / "result.json"


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="runden_auswertung",
        description="Eine Runde auswerten und das Ergebnis schreiben.")
    ap.add_argument("--runde", default=ROUND,
                    help=f"Runden-Kennung wie im Aufgabenregister (Vorgabe: {ROUND})")
    ap.add_argument("--stand", choices=("offen", "ausgezahlt"), default="offen",
                    help="offen vor der Auszahlung, ausgezahlt danach")
    ap.add_argument("--result-nach", default=None,
                    help="Zielpfad fuer result.json; ohne Angabe die feste "
                         "Adresse im moltrust-web-Checkout")
    ap.add_argument("--kein-result", action="store_true",
                    help="nur die Markdown-Auswertung, kein result.json")
    ap.add_argument("--vorwarnung", action="store_true",
                    help="nur melden, wie viele Einreichungen und wie viele "
                         "vollstaendige Gruppen vorliegen; nichts schreiben")
    ap.add_argument("--vorab", action="store_true",
                    help="result.json ohne Marktabruf: nur die Bedingungen, "
                         "damit die Adresse antwortet, bevor die Runde laeuft")
    a = ap.parse_args(argv)
    runde = a.runde

    now = datetime.now(timezone.utc)

    if a.vorwarnung:
        # T-24h. Sie schreibt nichts: eine Vorwarnung, die eine Datei
        # anfasst, aendert den Zustand, ueber den sie berichtet.
        gathered, unreadable = gather(runde)
        if not gathered:
            notify.send_telegram(
                f"MolTrust - Vorwarnung {runde}: keine Aufgabe abfragbar "
                f"({'; '.join(unreadable) or 'kein Eintrag im Register'})",
                channel=notify.ALERTS)
            print("Vorwarnung: keine Aufgabe abfragbar")
            return 2
        zeilen, summe_subs, summe_voll = [], 0, 0
        for spec, task, subs, kept, _c, _r in gathered:
            summe_subs += len(subs)
            if spec.get("profile") == "gruppe":
                aus = gruppen_auswertung(spec, kept)
                voll = aus["vollstaendig"]
                summe_voll += voll
                zeilen.append(f"  {spec['ref']}: {len(subs)} Einreichungen, "
                              f"{len(kept)} qualifiziert, "
                              f"{len(aus['kandidaten'])} genannte Gruppen, "
                              f"{voll} vollstaendig")
            else:
                zeilen.append(f"  {spec['ref']}: {len(subs)} Einreichungen, "
                              f"{len(kept)} qualifiziert")
        text = (f"MolTrust - Vorwarnung {runde}, 24 h vor Ablauf\n\n"
                + "\n".join(zeilen)
                + f"\n\nZusammen {summe_subs} Einreichungen, "
                f"{summe_voll} vollstaendige Gruppen.\n"
                f"Ergebnis: https://moltrust.ch/rounds/{runde}/result.json")
        if unreadable:
            text += "\n\nNICHT ABFRAGBAR:\n  " + "\n  ".join(unreadable)
        notify.send_telegram(text, channel=notify.ALERTS if unreadable
                             else notify.STATS)
        print(text)
        return 2 if unreadable else 0

    if a.vorab:
        # Kein Marktabruf: vor dem Erzeugen der Aufgaben gibt es dort nichts
        # zu lesen. Geschrieben wird allein, was schon entschieden ist — die
        # Bedingungen der Runde.
        if bedingungen(runde) is None:
            print(f"Keine Bedingungen fuer {runde} in RUNDEN — "
                  f"ein Vorablauf haette nichts zu sagen.")
            return 2
        doc = ergebnis(runde, [], {}, {}, stand=a.stand)
        ziel = pathlib.Path(a.result_nach) if a.result_nach \
            else result_pfad(runde)
        try:
            wie = schreibe_result(doc, str(ziel))
        except ValueError as e:
            print(f"result.json NICHT geschrieben: {e}")
            return 2
        print(f"Vorablauf {runde}: {ziel}")
        print(f"  Version {wie['version']}, ersetzt {wie['ersetzt']}, "
              f"{wie['grund']}")
        print(f"  Stand {doc['stand']}, Bedingungen "
              f"{doc['bedingungen']['fassung']}, noch keine Einreichungen")
        return 0

    # Reiner Filter, kein Abruf: wird unten fuer "x von y Tasks" gebraucht.
    pool = [t for t in TASKS if t.get("round") == runde]
    gathered, unreadable = gather(runde)
    if not gathered and unreadable:
        print(unreadable[0])
        return 2

    if unreadable:
        # Not measurable is not zero.
        notify.send_telegram(
            f"MolTrust - Auswertung {runde} unvollstaendig:\n  "
            + "\n  ".join(unreadable), channel=notify.ALERTS)
        print("NICHT ABFRAGBAR:\n  " + "\n  ".join(unreadable))
        return 2

    # The round-wide cap, over all three tasks together.
    per_task = {s["ref"]: k for s, _t, _su, k, _c, _r in gathered}
    capped = round_cap(per_task)

    # Eine Erzeugungsstelle: bezahlt, ohne Platz, abgewiesen und die
    # tx-Hashes in einer Datei. Die frueher getrennte
    # runde4-ohne-platz.json ist darin aufgegangen — drei Dateien
    # nebeneinander waren der Anlass, das zusammenzufuehren.
    leer = ohne_platz(gathered, capped)
    if not a.kein_result:
        zahlungen = auszahlungen(runde)
        doc = ergebnis(runde, gathered, capped, zahlungen, stand=a.stand)
        ziel = pathlib.Path(a.result_nach) if a.result_nach \
            else result_pfad(runde)
        try:
            wie = schreibe_result(doc, str(ziel))
        except ValueError as e:
            print(f"result.json NICHT geschrieben: {e}")
            return 2
        print(f"result.json: {ziel}")
        print(f"  Version {wie['version']}, ersetzt {wie['ersetzt']}, "
              f"{wie['grund']}")
        print(f"  bezahlt {len(doc['bezahlt'])}, ohne Platz "
              f"{len(doc['ohne_platz'])}, abgewiesen "
              f"{doc['abgleich']['abgewiesen']}, Abgleich "
              f"{doc['abgleich']['einreichungen']}")
        fehlt = [b for b in doc["bezahlt"] if not b["tx"]]
        if fehlt:
            print(f"  ohne tx-Hash: {len(fehlt)} von {len(doc['bezahlt'])} "
                  f"(Auszahlungsdatei fehlt oder die Runde ist noch offen)")

    md = [f"# Runde {runde} — Auswertung zum Ablauf",
          "",
          f"**Lauf:** {now.strftime('%Y-%m-%d %H:%M:%SZ')} · "
          f"**nichts ausgezahlt, nichts angenommen**",
          "",
          "Erzeugt von `scripts/runden_auswertung.py`. Der Lauf liest den Markt "
          "und die eigene Datenbank und schreibt diese Datei. Er ruft kein "
          "`accept-submissions` auf und bewegt kein Geld.",
          ""]

    grand_rows, grand_net, all_cut, all_no_place = [], 0.0, 0, 0
    all_rest = 0
    reason_tally: dict = defaultdict(int)
    own_hits = []

    for spec, task, subs, kept, cut, reasons in gathered:
        ref = spec["ref"]
        kept_c, cut_round = capped.get(ref, (kept, 0))
        if cut_round:
            reasons[f"Adresse hat schon einen Platz in Runde {runde}"] = cut_round
        # Nicht cut + cut_round, sondern die Summe der benannten Gruende:
        # `cut` steht seit dem 07.10. selbst als Grund in `reasons`, und wer
        # beide addiert, zaehlt ihn zweimal. Die Gruende sind jetzt die
        # Buchfuehrung, nicht die Zaehler daneben.
        total_cut = sum(reasons.values())
        all_cut += total_cut
        for r, n in reasons.items():
            reason_tally[r] += n

        winners = kept_c[:spec["slots"]]
        # Gueltig, qualifiziert, und trotzdem ohne Platz - weil die Aufgabe
        # zehn hat. Das ist keine Abweisung und wird getrennt gezaehlt: wer so
        # endet, hat alles richtig gemacht und war der elfte.
        no_place = kept_c[spec["slots"]:]
        all_no_place += len(no_place)
        rows, note = allocate_bps(winners)
        # Treffen die Teile die Grundgesamtheit? Was uebrig bleibt, sind
        # Einreichungen, die der Qualifizierer verworfen hat, ohne dass sein
        # Grund in `reasons` landete - meist mehrere Einreichungen derselben
        # DID, von denen nur die erste zaehlt. Es steht als eigener Posten da,
        # statt die Summe still nicht aufgehen zu lassen.
        rest = len(subs) - len(winners) - len(no_place) - total_cut
        all_rest += rest
        gross = spec["gross"]
        brutto_mikro = int(round(gross * 1_000_000))
        # Die Netto-Praemie als Summe der Empfaengerbetraege, nicht als
        # Prozentsatz auf die Praemie: der Vertrag schneidet je Empfaenger ab,
        # und die Summe der abgeschnittenen Betraege ist nicht der Prozentsatz
        # auf die Summe.
        net = (netto_mikro(brutto_mikro, 10000 // spec["slots"])
               * spec["slots"]) / 1_000_000
        grand_net += net if rows else 0.0

        exp = parse_iso(task.get("expiryTime"))
        md += [f"## {ref} — {spec['label']}",
               "",
               f"| | |",
               f"|---|---|",
               f"| Task | `{spec['id'][:18]}…` |",
               f"| Ablauf | {task.get('expiryTime')} |",
               f"| Status | {task.get('status')}"
               + (" — **abgelaufen**" if exp and now >= exp else "") + " |",
               f"| Einreichungen | **{len(subs)} von "
               f"{task.get('submissionCount')}** gelesen "
               f"(Grundgesamtheit = `task submissions`, kein LIMIT) |",
               f"| qualifiziert | **{len(kept)} von {len(subs)}** |",
               f"| nach Rundendeckel | **{len(kept_c)} von {len(kept)}** |",
               f"| bezahlte Plaetze | **{len(winners)} von {spec['slots']}** |",
               f"| gueltig ohne Platz | **{len(no_place)} von {len(kept_c)}** "
               f"— Platz vergeben (Rundendeckel {spec['slots']} je Aufgabe "
               f"erreicht) |",
               f"| Abgleich | {len(subs)} = {len(winners)} bezahlt + "
               f"{len(no_place)} ohne Platz + {total_cut} abgewiesen + "
               f"{len(subs) - len(winners) - len(no_place) - total_cut} ohne "
               f"aufgeschluesselten Grund |",
               f"| Praemie brutto | {gross:.3f} USDC |",
               f"| Praemie netto | {net:.6f} USDC (750 bp Gebuehr abgezogen) |",
               ""]
        if rows:
            md += [f"Anteilsregel: {note}.", "",
                   "| Adresse | bp | Betrag USDC | Grundlage |",
                   "|---|---:|---:|---|"]
            for r in rows:
                # Ganzzahlig und je Empfaenger, wie der Vertrag. Die frueher
                # hier stehende Fliesskomma-Rechnung auf die ganze Praemie lag
                # 5 Mikro-USDC je Aufgabe darunter.
                amount = netto_mikro(brutto_mikro, r["bps"]) / 1_000_000
                basis = (f"{r['slots']} Platz" if r["slots"] == 1
                         else f"{r['slots']} Plaetze")
                basis += f", erste Einreichung {r['at']}, DID {r['dids'][0]}"
                md.append(f"| `{r['addr']}` | {r['bps']} | {amount:.6f} | "
                          f"{basis} |")
                grand_rows.append((ref, r["addr"], r["bps"], amount, basis))
                if r["addr"].lower() in OURS:
                    own_hits.append((ref, r["addr"]))
            md.append("")
        else:
            md += ["**Keine Gewinner.** Keine Auszahlungszeile für diesen Task.",
                   ""]

        if reasons:
            md += [f"Abgewiesen: **{total_cut} von {len(subs)}**",
                   "", "| Grund | Zahl |", "|---|---:|"]
            for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
                md.append(f"| {r} | {n} |")
            md.append("")

    total_subs = sum(len(s) for _sp, _t, s, _k, _c, _r in gathered)
    md[4:4] = [
        f"**Summe:** {sum(r[3] for r in grand_rows):.6f} USDC an "
        f"{len({r[1] for r in grand_rows})} Adressen auf "
        f"{len(grand_rows)} Zeilen über {len(gathered)} von {len(pool)} Tasks. "
        f"Abgewiesen {all_cut} von {total_subs} Einreichungen, "
        f"{all_no_place} von {total_subs} gueltig ohne Platz "
        f"(Platz vergeben, Rundendeckel erreicht), "
        f"{all_rest} von {total_subs} ohne aufgeschluesselten Grund. "
        f"Abgleich: {len(grand_rows)} + {all_no_place} + {all_cut} + "
        f"{all_rest} = {len(grand_rows) + all_no_place + all_cut + all_rest} "
        f"von {total_subs}.",
        ""]

    md += ["---", "", "## Zählregel und Gegenproben", "",
           f"**Grundgesamtheit.** Jede Zahl oben zählt gegen `task submissions` "
           f"des Marktes, nicht gegen eine gefilterte Sicht. Gelesen wurden "
           f"{total_subs} Zeilen; die Marktangabe `submissionCount` je Task "
           f"steht in der jeweiligen Tabelle daneben. Weichen die beiden ab, "
           f"ist die Lesung unvollständig und diese Datei nicht belastbar.",
           "",
           f"**Sind wir in der eigenen Messung?** Geprüft wurden alle "
           f"Auszahlungsadressen gegen unsere vier bekannten Wallets. "
           + (f"**Treffer: {len(own_hits)}** — "
              + ", ".join(f"{r} {a}" for r, a in own_hits)
              if own_hits else "Kein Treffer."),
           "",
           f"**Stichtag.** {now.strftime('%Y-%m-%d %H:%M:%SZ')}. Alle Zahlen "
           f"sind der Stand dieses Laufs; der Markt kann danach weitere "
           f"Einreichungen annehmen, solange ein Task offen ist.",
           "",
           "## Was jetzt nicht passiert ist", "",
           "Keine Annahme, keine Auszahlung, keine Transaktion. Die Zeilen oben "
           "sind ein Vorschlag. Nach Freigabe wird daraus **ein** Aufruf je "
           "Task.", ""]

    ziel_md = out_pfad(runde)
    os.makedirs(os.path.dirname(ziel_md), exist_ok=True)
    with open(ziel_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")

    total = sum(r[3] for r in grand_rows)
    # Die Runde kommt aus dem Argument. Bis zum 11.10.2026 stand hier
    # "Runde 4", und ein r5-Lauf meldete nach Telegram, Runde 4 sei
    # ausgewertet — der dritte Rest, der trotz der Umbenennung auf eine
    # feste Runde zeigte, und der erste, der nach draussen ging.
    msg = (f"MolTrust - Runde {runde} ausgewertet "
           f"({now.strftime('%d.%m. %H:%MZ')})\n\n"
           f"Auszahlungssumme   {total:.6f} USDC\n"
           f"Zeilen             {len(grand_rows)} auf "
           f"{len({r[1] for r in grand_rows})} Adressen\n"
           f"Abgewiesen         {all_cut} von {total_subs} Einreichungen\n"
           f"Gueltig ohne Platz {all_no_place} von {total_subs}\n"
           f"Tasks              {len(gathered)} von {len(pool)}\n\n"
           f"Nichts ausgezahlt. Liste: {ziel_md}\n"
           f"Freigabe durch Lars, danach ein Aufruf je Task.")
    notify.send_telegram(msg, channel=notify.MONEY)
    print(msg)
    print(f"\ngeschrieben: {ziel_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
