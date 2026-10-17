#!/usr/bin/env python3
"""Was aus einem Escrow geworden ist, gegen das, was pool_spend behauptet.

    python3 scripts/escrow_reconcile.py            # Bericht, schreibt nichts
    python3 scripts/escrow_reconcile.py --apply    # abgeschlossene Zeilen fortschreiben
    python3 scripts/escrow_reconcile.py --json

Exit 0 jede Escrow-Zeile zugeordnet, 1 eine Zeile ohne Zuordnung oder eine
Teilauszahlung, 2 Datenbank, Markt oder Kette nicht lesbar.

f-no-stale-escrow meldete am 16.10.2026 fuenf Zeilen, drei davon zu Unrecht:
die drei Runde-4-Aufgaben waren am 9.10. um 17:17 UTC abgerechnet, je zehn
Empfaenger und die vollen 541000 Mikro-USDC aus dem Escrow heraus (Bloecke
52388451/53/55). In pool_spend stand weiter `escrowed`. Kein Pfad im Repo
schreibt diesen Zustand je fort: Runde 3 wurde am 3.10. von Hand nachgezogen,
Runde 4 nicht. Eine Buchhaltung, die von Hand nachgezogen wird, laeuft
irgendwann auseinander, und dann meldet die Invariante drei Betraege als
liegend, die seit einer Woche bei den Arbeitern sind — und wer das einmal
gesehen hat, liest die vierte Meldung nicht mehr.

Die Zuordnung Zeile -> Aufgabe kommt nicht aus einer gepflegten Liste.
`pool_spend.tx_hash` ist die Escrow-Transaktion; ihre Logs tragen die Task-ID
als erstes Topic. Jeder Kandidat wird beim Markt nachgefragt und nur
angenommen, wenn dessen `escrowTxHash` wieder genau diese Transaktion nennt.
Eine Liste im Code waere eine zweite Wahrheit neben task_watch.py und haette
die Runde-2-Aufgabe nicht gekannt, die dort nie stand.

Geschrieben wird nur `spent` oder `refunded`, nur auf Zeilen, die noch
`escrowed` sind, und nur wenn die Kette die Abrechnung bestaetigt: jede
Award-Transaktion existiert, ist erfolgreich, und sie bewegt mindestens den
Escrow-Betrag in USDC. Eine Teilauszahlung wird gemeldet und nicht
geschrieben — wie eine Praemie aufgeteilt wird, ist keine Buchhaltungsfrage.
Nichts hier nimmt an, zahlt, erstattet, verlaengert oder loescht.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
# Transfer(address,address,uint256)
XFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO32 = "0x" + "0" * 64

# Die Zustaende, die diese Datei schreiben darf. `escrowed` steht nicht darin:
# ein Escrow entsteht beim Anlegen der Aufgabe, nicht beim Abgleich.
SCHREIBBAR = ("spent", "refunded")

# Die einzige schreibende Anweisung, als Konstante. Zustand und Zeilennummer
# gehen als psql-Variablen hinein, nicht in den Text — eine zusammengesetzte
# SQL-Zeile waere hier ohne Not und bandit haette recht, sie zu melden. Der
# zweite Teil der WHERE-Klausel macht den Lauf wiederholbar: eine Zeile, die
# jemand zwischendurch fortgeschrieben hat, wird nicht ein zweites Mal
# angefasst.
UPDATE_SQL = ("UPDATE pool_spend SET state = :'neu' "
              "WHERE id = :zid AND state = 'escrowed'")


def rpc(method, params):
    # Kein Rueckfall auf einen oeffentlichen Knoten (CLAUDE.md, 09.10.2026).
    # Fehlt die Variable, bricht der Aufruf ab: ein stiller Wechsel auf
    # mainnet.base.org verdeckt, dass der eigene Knoten nicht antwortet, und
    # sieht in jedem anderen Signal gesund aus. Hier entscheidet die Antwort,
    # ob eine Geldbuchung fortgeschrieben wird.
    url = os.environ.get("BASE_RPC")
    if not url:
        raise SystemExit("BASE_RPC fehlt — ohne eigenen Knoten keine Auskunft")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310  # nosec B310
        return json.loads(r.read()).get("result")


def cli(*args, timeout=120):
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.npm-global/bin") + ":" + env.get("PATH", "")
    out = subprocess.run(["taskmarket", *args], capture_output=True, text=True,
                         timeout=timeout, env=env)
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {}


def psql(sql):
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-F", "\t", "-c", sql],
        capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise SystemExit(f"psql: {out.stderr[:200]}")
    return [ln.split("\t") for ln in out.stdout.strip().split("\n") if ln.strip()]


def fortschreiben(zeile_id, neu):
    """Den Zustand einer Escrow-Zeile fortschreiben, sonst nichts.

    Kein Betrag, kein Zweck, kein tx_hash: `tx_hash` bleibt die
    Escrow-Transaktion, weil die Zeile von ihr handelt. Die Abrechnung steht
    im Bericht und nicht an ihrer Stelle.
    """
    if neu not in SCHREIBBAR:
        raise SystemExit(f"Zustand {neu!r} wird hier nicht geschrieben")
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-t", "-v", "neu=" + neu, "-v", "zid=" + str(int(zeile_id)),
         "-c", UPDATE_SQL],
        capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise SystemExit(f"psql: {out.stderr[:200]}")
    return out.stdout.strip()


def offene_zeilen():
    return [{"id": int(r[0]), "usdc": r[1], "mikro": mikro(r[1]),
             "tx": (r[2] or "").strip(), "alter_tage": float(r[3]),
             "zweck": r[4]}
            for r in psql("""
        SELECT id, usdc, coalesce(tx_hash, ''),
               round((extract(epoch from (now()-spent_at))/86400)::numeric, 2),
               left(purpose, 60)
          FROM pool_spend WHERE state = 'escrowed' ORDER BY spent_at, id""")]


def mikro(usdc_text):
    """Mikro-USDC aus der numeric(18,6)-Spalte, ohne Fliesskomma.

    `float(x) * 1e6` auf 0.541 ergibt 540999.9999999999 und damit nach int()
    eine Zeile, die um ein Mikro nicht abgerechnet ist. Der Betrag entscheidet
    hier, ob eine Buchung fortgeschrieben wird, also wird er nicht geschaetzt.
    """
    ganz, _, bruch = str(usdc_text).strip().partition(".")
    return int(ganz) * 1_000_000 + int((bruch + "000000")[:6] or 0)


def task_id_kandidaten(receipt):
    """Was in den Logs der Escrow-Transaktion eine Task-ID sein kann.

    Das erste Topic jedes Logs, ohne die, die keine ID sein koennen: die
    Nullstelle und alles, was eine linksgefuellte Adresse ist — in der
    USDC-Transfer-Zeile steht dort der Absender. Welches Ereignis die ID
    traegt, steht hier absichtlich nicht: die Signatur ihres Diamanten ist
    nicht unsere, und eine falsch geratene Signatur liest sich wie eine Zeile
    ohne Zuordnung. Bestaetigt wird jeder Kandidat beim Markt.
    """
    aus = []
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if len(topics) < 2:
            continue
        t = topics[1].lower()
        if t == ZERO32 or t.startswith("0x" + "0" * 24):
            continue
        if t not in aus:
            aus.append(t)
    return aus


def aufgabe_zu(tx_hash, hole_receipt=None, hole_task=None):
    """Die Aufgabe, deren Escrow genau diese Transaktion ist, oder None."""
    hole_receipt = hole_receipt or (lambda h: rpc("eth_getTransactionReceipt", [h]))
    hole_task = hole_task or (lambda t: (cli("task", "get", t) or {}).get("data"))
    receipt = hole_receipt(tx_hash)
    if not receipt:
        return None
    for kandidat in task_id_kandidaten(receipt):
        d = hole_task(kandidat)
        if d and str(d.get("escrowTxHash", "")).lower() == tx_hash.lower():
            return d
    return None


def abrechnung_gedeckt(task, receipts, mikro_soll):
    """(gedeckt, text) — ob die Kette die Awards des Marktes traegt.

    Verlangt wird je Abrechnungs-Transaktion ein erfolgreicher Receipt, der
    mindestens den Escrow-Betrag in USDC bewegt. Mindestens und nicht genau:
    eine Transaktion kann mehrere Aufgaben abrechnen, und dann liegt die Summe
    darueber. Nach unten gibt es keinen Spielraum — weniger bewegt heisst
    weniger ausgezahlt, und das ist keine abgeschlossene Buchung.
    """
    txs, brutto = [], 0
    for a in task.get("awards") or []:
        brutto += int(a.get("grossAmount") or 0)
        h = a.get("settlementTxHash")
        if h and h not in txs:
            txs.append(h)
    if brutto != mikro_soll:
        return False, f"Awards summieren {brutto}, Escrow ist {mikro_soll}"
    if not txs:
        return False, "Awards ohne Abrechnungs-Transaktion"
    for h in txs:
        r = receipts.get(h)
        if not r:
            return False, f"kein Receipt zu {h[:12]}"
        if str(r.get("status")) != "0x1":
            return False, f"{h[:12]} ist nicht erfolgreich ({r.get('status')})"
        bewegt = sum(int(l["data"], 16) for l in (r.get("logs") or [])
                     if str(l.get("address", "")).lower() == USDC
                     and (l.get("topics") or [""])[0] == XFER)
        if bewegt < mikro_soll:
            return False, f"{h[:12]} bewegt {bewegt} statt {mikro_soll}"
    return True, (f"{len(task['awards'])} Awards, {brutto} Mikro-USDC, "
                  f"tx {' '.join(h[:12] for h in txs)}")


def befund(zeile, task, receipts):
    """(neuer_zustand oder None, text) fuer eine Escrow-Zeile."""
    if task is None:
        return None, "keine Aufgabe zu dieser Escrow-Transaktion gefunden"
    ref = task.get("referenceCode") or "?"
    status = str(task.get("status") or "?")
    phase = str(task.get("phase") or "?")
    awards = task.get("awards") or []
    if status in ("cancelled", "canceled", "refunded") and not awards:
        return "refunded", f"{ref}: {status}, kein Award — Escrow zurueck"
    if awards:
        gedeckt, wie = abrechnung_gedeckt(task, receipts, zeile["mikro"])
        if gedeckt:
            return "spent", f"{ref}: {status}/{phase}, abgerechnet — {wie}"
        return None, f"{ref}: {status}/{phase}, Awards ohne volle Deckung — {wie}"
    return None, (f"{ref}: {status}/{phase}, kein Award, {zeile['alter_tage']:.1f} "
                  f"Tage — Escrow liegt wirklich")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="abgeschlossene Zeilen in pool_spend fortschreiben")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        zeilen = offene_zeilen()
    except SystemExit as exc:
        print(f"Datenbank nicht lesbar: {exc}", file=sys.stderr)
        return 2

    bericht, ungeklaert = [], 0
    for z in zeilen:
        if not z["tx"]:
            bericht.append({**z, "neu": None, "text": "Zeile ohne tx_hash"})
            ungeklaert += 1
            continue
        task = aufgabe_zu(z["tx"])
        receipts = {}
        for a in (task or {}).get("awards") or []:
            h = a.get("settlementTxHash")
            if h and h not in receipts:
                receipts[h] = rpc("eth_getTransactionReceipt", [h])
        neu, text = befund(z, task, receipts)
        if neu is None and (task is None or (task.get("awards") or [])):
            ungeklaert += 1
        bericht.append({**z, "neu": neu, "text": text,
                        "ref": (task or {}).get("referenceCode")})

    geschrieben = []
    if args.apply:
        for b in bericht:
            if b["neu"] not in SCHREIBBAR:
                continue
            geschrieben.append({"id": b["id"], "neu": b["neu"],
                                "psql": fortschreiben(b["id"], b["neu"])})

    if args.json:
        print(json.dumps({"zeilen": bericht, "geschrieben": geschrieben,
                          "angewandt": args.apply}, indent=2))
    else:
        if not bericht:
            print("Keine Escrow-Zeile in pool_spend.")
        for b in bericht:
            marke = {"spent": "ABGERECHNET", "refunded": "ERSTATTET"}.get(
                b["neu"], "LIEGT" if b["neu"] is None else b["neu"])
            print(f"[{marke}] #{b['id']} {b['usdc']} USDC — {b['text']}")
        if args.apply:
            print(f"\nFortgeschrieben: {len(geschrieben)} Zeile(n) "
                  f"{geschrieben or '—'}")
        elif any(b["neu"] in SCHREIBBAR for b in bericht):
            print("\nNichts geschrieben. --apply schreibt die abgerechneten "
                  "und erstatteten Zeilen fort.")
    return 1 if ungeklaert else 0


if __name__ == "__main__":
    sys.exit(main())
