#!/usr/bin/env python3
"""Runde 4 auswerten, wenn die Frist laeuft - und nichts auszahlen.

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
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notify  # noqa: E402
from scripts.task_watch import (  # noqa: E402
    ELIGIBLE,
    FEE_BPS,
    TASKS,
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
OUT = os.path.expanduser("~/Downloads/runde4-auswertung.md")

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
    summe_bezahlt = summe_gebuehr = 0
    einreichungen = 0

    for spec, task, subs, kept, _cut, reasons in gathered:
        ref = spec["ref"]
        kept_c, cut_round = capped.get(ref, (kept, 0))
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
    return {
        "runde": runde,
        "stand": stand,
        "version": 1,
        "ersetzt": None,
        "erzeugt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quelle": "scripts/runden_auswertung.py",
        "rundendeckel": {
            "plaetze_je_aufgabe": (gathered[0][0]["slots"] if gathered else None),
            "plaetze_je_adresse_je_runde": (gathered[0][0].get("cap")
                                            if gathered else None),
        },
        "aufgaben": aufgaben,
        "bezahlt": bezahlt,
        "ohne_platz": leer,
        "abgewiesen": abgewiesen,
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
    a = ap.parse_args(argv)
    runde = a.runde

    now = datetime.now(timezone.utc)
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

    md = [f"# Runde 4 — Auswertung zum Ablauf",
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
        net = gross * (1 - FEE_BPS / 10000)
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
                amount = net * r["bps"] / 10000
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

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")

    total = sum(r[3] for r in grand_rows)
    msg = (f"MolTrust - Runde 4 ausgewertet ({now.strftime('%d.%m. %H:%MZ')})\n\n"
           f"Auszahlungssumme   {total:.6f} USDC\n"
           f"Zeilen             {len(grand_rows)} auf "
           f"{len({r[1] for r in grand_rows})} Adressen\n"
           f"Abgewiesen         {all_cut} von {total_subs} Einreichungen\n"
           f"Gueltig ohne Platz {all_no_place} von {total_subs}\n"
           f"Tasks              {len(gathered)} von {len(pool)}\n\n"
           f"Nichts ausgezahlt. Liste: {OUT}\n"
           f"Freigabe durch Lars, danach ein Aufruf je Task.")
    notify.send_telegram(msg, channel=notify.MONEY)
    print(msg)
    print(f"\ngeschrieben: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
