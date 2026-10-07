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

ROUND = "r4"
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


def main() -> int:
    now = datetime.now(timezone.utc)
    pool = [t for t in TASKS if t.get("round") == ROUND]
    if not pool:
        print(f"Keine Tasks mit round={ROUND} im Register.")
        return 2

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

    if unreadable:
        # Not measurable is not zero.
        notify.send_telegram(
            "MolTrust - Runde-4-Auswertung unvollstaendig:\n  "
            + "\n  ".join(unreadable), channel=notify.ALERTS)
        print("NICHT ABFRAGBAR:\n  " + "\n  ".join(unreadable))
        return 2

    # The round-wide cap, over all three tasks together.
    per_task = {s["ref"]: k for s, _t, _su, k, _c, _r in gathered}
    capped = round_cap(per_task)

    md = [f"# Runde 4 — Auswertung zum Ablauf",
          "",
          f"**Lauf:** {now.strftime('%Y-%m-%d %H:%M:%SZ')} · "
          f"**nichts ausgezahlt, nichts angenommen**",
          "",
          "Erzeugt von `scripts/runde4_auswertung.py`. Der Lauf liest den Markt "
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
            reasons[f"Adresse hat schon einen Platz in Runde {ROUND}"] = cut_round
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
