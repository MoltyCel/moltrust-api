#!/usr/bin/env python3
"""What the chain says we spent, against what we wrote down.

    python3 scripts/wallet_reconcile.py            # human-readable
    python3 scripts/wallet_reconcile.py --json     # for the Sunday report

Exit 0 reconciled, 1 difference, 2 could not read one of the two sides.

Two consoles work these wallets and do not see each other. On 2026-09-21 a
5 USDC outflow looked unattributed for long enough to stop work over — it was
recorded in pool_spend the whole time, by the other session, and nobody had
compared the two sides. This is that comparison, run weekly.

Matching is deliberately not by hash alone. `pool_spend.tx_hash` holds two
different kinds of hash and both are legitimate:

  * a USDC Transfer, when the payout was a direct transfer
  * a *relay* transaction, when the payout went through the TaskMarket
    contract — the relayer's hash causes the transfer but is not the transfer

Reconciling on hashes alone flags every relayed payout as missing from the
chain, which is a false alarm that trains people to ignore the real one. So
amounts per wallet are the primary comparison and hashes are reported as
supporting detail.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
BLOCKSCOUT = "https://base.blockscout.com/api/v2"

# Enough for a busy bounty round; beyond it the script refuses rather than
# reporting from a truncated history.
MAX_PAGES = 40

# The wallets the console is allowed to spend from, and the pool each belongs
# to. An address not listed here is not reconciled, on purpose: this compares
# governed spending, and quietly widening it would hide the day somebody spends
# from a wallet nobody agreed on.
GOVERNED = {
    "0xd8f5bB747f7459BF3e1cc1aD041E2cA57B946C38": "test-wallet",
    "0xa175d51bfe0170738720DAAEc627A84d44dc9Eb9": "taskmarket",
}

# Two budgets share the test wallet, and they are never netted against each
# other. One balance, two ceilings — so the reconciliation has to attribute
# every outflow to a budget before it can say what is left of either.
#
# The attribution comes from `pool_spend.purpose`: a booking whose purpose
# starts with "defect-bounty" belongs to the defect pot, everything else to the
# original test/bounty budget. An outflow with no booking at all is attributed
# to the original budget, which is the stricter of the two — an unattributed
# spend is already the alarm, and guessing it into the roomier pot would soften
# the one number that must not be soft.
DEFECT_BOUNTY_PREFIX = "defect-bounty"

BUDGETS = {
    # Cumulative ceiling, raised from 16 to 21 on 2026-09-21 (Freigabe Lars)
    # after the wallet was topped up by 5 USDC for the bounty payouts. Closed
    # for bounties on 2026-09-21 with 0.75 left.
    "test/bounty-r1": {
        "cap": 21.0,
        "monthly": None,
        "note": "geschlossen fuer Bounties, Rest laeuft aus",
    },
    # 10 USDC a month, October to December 2026, cap 30 (Freigabe Lars
    # 2026-09-21). The month's remainder expires; it does not accumulate.
    DEFECT_BOUNTY_PREFIX: {
        "cap": 30.0,
        "monthly": 10.0,
        "note": "Monatsrest verfaellt",
    },
}

# Nothing may be paid out of the defect pot until the rules page is published.
# A bounty offered without published rules is an invitation to argue about them
# afterwards. Flip this to the publication date once moltrust.ch/defects is
# live and Lars has approved the text; until then any defect-bounty booking is
# reported as a rule break rather than as spending.
DEFECT_BOUNTY_RELEASED_ON = None

# Kept for callers that still read the old name.
TEST_WALLET_CAP_USDC = BUDGETS["test/bounty-r1"]["cap"]

UA = {"User-Agent": "moltrust-reconcile/1.0 (+https://moltrust.ch)", "Accept": "application/json"}

BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")


def usdc_balance(wallet: str) -> float | None:
    """Live USDC balance, read from the chain rather than from our books.

    The reconciliation compares two records of the past. A balance is the one
    figure neither record can be wrong about, and it is what settled the
    6.45 USDC phantom difference on 2026-09-21. It also catches the opposite
    case: money arriving in a governed wallet that nobody mentioned.
    """
    data = "0x70a08231" + "0" * 24 + wallet.lower().removeprefix("0x")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": USDC, "data": data}, "latest"]}).encode()
    req = urllib.request.Request(BASE_RPC, data=body,
                                 headers={**UA, "Content-Type": "application/json"})
    try:
        if not BASE_RPC.startswith("https://"):
            return None
        with urllib.request.urlopen(req, timeout=30) as response:  # nosec B310 - scheme checked above
            return int(json.loads(response.read())["result"], 16) / 1e6
    except Exception:
        return None


def chain_outflows(wallet: str) -> list[dict]:
    """Real USDC leaving this wallet.

    Token address is compared, never the symbol: the wallet has received
    several airdropped tokens whose symbol is 'USDC' written with homoglyphs
    (Cyrillic C and the like). A symbol filter counts those as ours.
    """
    # Paginated. One page holds 50 transfers, and after a bounty round this
    # wallet has more than that in a single afternoon. Reading one page and
    # calling it the total understated the chain by 6.45 USDC and turned a
    # correct book into a false alarm — the third time a partial read has been
    # handed on as a complete one, which is why the cap below refuses to guess.
    items = []
    params = ""
    for _ in range(MAX_PAGES):
        url = f"{BLOCKSCOUT}/addresses/{wallet}/token-transfers?type=ERC-20{params}"
        req = urllib.request.Request(url, headers=dict(UA))
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — scheme validated above  # nosec B310 - BLOCKSCOUT is a module constant
            body = json.loads(r.read())
        items.extend(body.get("items", []))
        nxt = body.get("next_page_params")
        if not nxt:
            break
        params = "&" + urllib.parse.urlencode(nxt)
    else:
        # Refusing to answer beats answering from a partial history.
        raise RuntimeError(f"{wallet}: mehr als {MAX_PAGES} Seiten, kein vollstaendiger Verlauf")

    # Paging to the end is not the same as seeing everything. On 2026-09-21 the
    # explorer's index for this address stopped at block 51 606 562 while the
    # chain was 500 blocks further on, and it still answered
    # next_page_params: null — 16 of 68 recognition payouts and a 10 USDC
    # top-up were simply absent, with no field saying so. Nothing inside the
    # response distinguishes "that is all there is" from "that is all I have".
    #
    # The balance does. Every USDC that ever entered or left this wallet is in
    # that one number, and the node serves it from state rather than from an
    # index. Inbound minus outbound has to equal it; where it does not, the
    # listing is stale and no figure derived from it may be reported.
    inbound = outbound = 0.0
    out = []
    for t in items:
        token = t.get("token") or {}
        addr = (token.get("address_hash") or token.get("address") or "").lower()
        if addr != USDC:
            continue
        value = int((t.get("total") or {}).get("value") or 0) / 10 ** 6
        if value == 0:
            # Zero-value transfers are address-poisoning noise, not spending.
            continue
        sender = ((t.get("from") or {}).get("hash") or "").lower()
        recipient = ((t.get("to") or {}).get("hash") or "").lower()
        if recipient == wallet.lower() and sender != wallet.lower():
            inbound += value
        if sender != wallet.lower():
            continue
        outbound += value
        to = (t.get("to") or {}).get("hash") or ""
        out.append({
            "ts": (t.get("timestamp") or "")[:19],
            "usdc": value,
            "to": to,
            "tx": t.get("transaction_hash"),
            # Moving money from one governed wallet to another is funding, not
            # spending. Counting it double-books: the 10 USDC that left the
            # test wallet for the taskmarket escrow is spent again when the
            # escrow pays a worker, and a naive sum reports 20.
            "internal": to.lower() in {w.lower() for w in GOVERNED},
        })

    balance = usdc_balance(wallet)
    if balance is None:
        raise RuntimeError(f"{wallet}: Kontostand nicht lesbar, Explorer-Sicht nicht pruefbar")
    implied = round(inbound - outbound, 6)
    if abs(implied - balance) > 0.000002:
        raise RuntimeError(
            f"{wallet}: Explorer-Sicht unvollstaendig. Zufluss {inbound:.6f} minus Abfluss "
            f"{outbound:.6f} ergibt {implied:.6f}, der Kontostand ist {balance:.6f} "
            f"(Abweichung {balance - implied:+.6f}). Der Index hinkt nach; "
            f"spaeter erneut laufen lassen."
        )
    return out


def budget_of(purpose: str) -> str:
    """Which pot a booking belongs to, by its purpose.

    Lars fixed the wording: `defect-bounty YYYY-MM`. Matching the prefix rather
    than the exact string means a month suffix, a typo in the month or a
    trailing note still lands in the right pot — mis-filing a defect payout as
    test spending would make the closed budget look overdrawn and the open one
    look untouched.

    The match is deliberately narrow, and the reason is sitting in the book
    already: bounty round 1 booked 78 payouts on 2026-09-21 with purposes like
    `Defekt-Bonus Bounty-Runde 1 (SUB-…)`. That is German, it is the *old*
    budget, and a looser match on "defekt" or "bonus" would have moved 5.25
    USDC of spent money into a pot that has not paid out a cent. Only the
    exact ASCII prefix counts.
    """
    return (DEFECT_BOUNTY_PREFIX
            if (purpose or "").strip().lower().startswith(DEFECT_BOUNTY_PREFIX)
            else "test/bounty-r1")


def split_by_budget(outflows: list[dict], book: list[dict], month: str) -> dict:
    """Attribute every outflow to a budget and total each one.

    `outflows` are chain rows (the truth about what left), `book` are
    `pool_spend` rows (the only place a purpose exists). The join is the
    transaction hash. An outflow with no booking is attributed to
    test/bounty-r1 and counted in `unattributed` so the reader can see the
    total is carrying a guess.
    """
    purpose_by_tx = {r["tx"]: r["purpose"] for r in book if r.get("tx")}
    totals = {name: 0.0 for name in BUDGETS}
    this_month = {name: 0.0 for name in BUDGETS}
    unattributed = 0.0

    for row in outflows:
        purpose = purpose_by_tx.get(row["tx"])
        if purpose is None:
            unattributed += row["usdc"]
            name = "test/bounty-r1"
        else:
            name = budget_of(purpose)
        totals[name] += row["usdc"]
        if (row.get("ts") or "")[:7] == month:
            this_month[name] += row["usdc"]

    out = {}
    for name, spec in BUDGETS.items():
        spent = round(totals[name], 6)
        entry = {
            "spent": spent,
            "cap": spec["cap"],
            "remaining": round(spec["cap"] - spent, 6),
            "note": spec["note"],
        }
        if spec["monthly"] is not None:
            used = round(this_month[name], 6)
            entry["monthly"] = spec["monthly"]
            entry["month"] = month
            entry["month_spent"] = used
            entry["month_remaining"] = round(spec["monthly"] - used, 6)
        out[name] = entry
    out["_unattributed"] = round(unattributed, 6)
    return out


def recorded(conn_str: str) -> list[dict]:
    import psycopg2  # imported late: the chain side works without a database

    with psycopg2.connect(conn_str) as conn, conn.cursor() as cur:
        cur.execute("SELECT pool, usdc, tx_hash, purpose, spent_at, state "
                    "FROM pool_spend ORDER BY spent_at")
        return [
            {"pool": p, "usdc": float(u), "tx": h, "purpose": (pu or "")[:120],
             "ts": s.isoformat()[:19] if s else None, "state": st}
            for p, u, h, pu, s, st in cur.fetchall()
        ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", "postgresql://moltstack@localhost/moltstack"))
    args = ap.parse_args()

    try:
        chain = {w: chain_outflows(w) for w in GOVERNED}
    except Exception as exc:  # noqa: BLE001
        print(f"UNGEPRUEFT — Kette nicht lesbar: {exc}", file=sys.stderr)
        return 2

    try:
        book = recorded(args.dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"UNGEPRUEFT — pool_spend nicht lesbar: {exc}", file=sys.stderr)
        return 2

    booked_hashes = {r["tx"] for r in book if r["tx"]}
    month = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")
    # Against the chain, everything that left a wallet counts — an escrow
    # deposit moved real money. The split matters for the other question, which
    # is how much is gone for good.
    result = {
        "wallets": [], "unbooked": [],
        "booked_total": round(sum(r["usdc"] for r in book), 6),
        "by_state": {st: round(sum(r["usdc"] for r in book if r["state"] == st), 6)
                     for st in ("spent", "escrowed", "refunded")},
    }

    chain_total = 0.0
    for wallet, pool in GOVERNED.items():
        rows = chain[wallet]
        external = [r for r in rows if not r["internal"]]
        internal = [r for r in rows if r["internal"]]
        spent = round(sum(r["usdc"] for r in external), 6)
        moved = round(sum(r["usdc"] for r in internal), 6)
        chain_total += spent
        result["wallets"].append({
            "wallet": wallet, "pool": pool, "outflows": len(external), "usdc": spent,
            "internal_transfers": len(internal), "internal_usdc": moved,
            # The cap counts internal transfers too: the money really did
            # leave the test wallet, and the exception is about that wallet's
            # balance, not about where the money went next.
            "balance": usdc_balance(wallet),
            # Two budgets share this wallet. The cap counts internal transfers
            # too: the money really did leave, and the exception is about this
            # wallet's balance, not about where the money went next.
            **({"budgets": split_by_budget(external + internal, book, month)}
               if pool == "test-wallet" else {}),
        })
        for r in external:
            if r["tx"] not in booked_hashes:
                result["unbooked"].append({**r, "wallet": wallet, "pool": pool})

    result["chain_total"] = round(chain_total, 6)
    result["difference"] = round(chain_total - result["booked_total"], 6)

    if args.json:
        print(json.dumps(result))
    else:
        for w in result["wallets"]:
            line = f"{w['pool']:<12} {w['wallet'][:10]}…  {w['outflows']:>2} Ausgaben  {w['usdc']:>8.4f} USDC"
            if w.get("internal_usdc"):
                line += f"  + {w['internal_usdc']:.4f} intern umgebucht"
            if w["balance"] is not None:
                line += f"  | Kontostand {w['balance']:.6f} USDC"
            print(line)
            for name, b in (w.get("budgets") or {}).items():
                if name.startswith("_"):
                    continue
                detail = (f"             {name:<16} Deckel {b['cap']:>5.1f}  "
                          f"verbraucht {b['spent']:>7.4f}  Rest {b['remaining']:>7.4f}")
                if "monthly" in b:
                    detail += (f"  | {b['month']}: {b['month_spent']:.4f} von "
                               f"{b['monthly']:.1f}, Rest {b['month_remaining']:.4f}")
                print(detail + f"   ({b['note']})")
            # Escrow that was deposited and never claimed sits here and is easy
            # to forget: 0xa175 held 0.048 USDC after the first bounty round.
            if w["pool"] == "taskmarket" and w["balance"]:
                print(f"             Restguthaben der Escrow-Wallet: {w['balance']:.6f} USDC "
                      f"— eingezahlt, nicht ausgezahlt, nicht zurueckgeholt.")
            # Money arriving in a governed wallet without a raised ceiling is
            # not headroom. On 2026-09-21 the test wallet was topped up by
            # 10 USDC while 0.75 of the cap was left; the cap is the limit.
            budgets = w.get("budgets") or {}
            if budgets and w["balance"] is not None:
                headroom = sum(b["remaining"] for n, b in budgets.items()
                               if not n.startswith("_"))
                if w["balance"] > headroom + 0.0001:
                    print(f"             Kontostand liegt {w['balance'] - headroom:.4f} USDC ueber der "
                          f"Summe beider Deckel-Reste. Eine Aufstockung hebt keinen Deckel — "
                          f"es gelten {headroom:.4f}, bis Lars etwas anderes sagt.")
            if budgets.get("_unattributed", 0) > 0.0001:
                print(f"             {budgets['_unattributed']:.4f} USDC ohne Buchung und damit ohne "
                      f"Zweck — dem geschlossenen Topf zugeschlagen, weil ein nicht "
                      f"zuzuordnender Abfluss ohnehin der Alarm ist.")
            defect = budgets.get(DEFECT_BOUNTY_PREFIX)
            if defect and defect["spent"] > 0.0001 and DEFECT_BOUNTY_RELEASED_ON is None:
                print(f"             REGELBRUCH: {defect['spent']:.4f} USDC aus dem Defekt-Topf "
                      f"gezahlt, bevor die Regeln-Seite freigegeben ist. Ein Bounty ohne "
                      f"veroeffentlichte Regeln laedt dazu ein, sie nachher zu bestreiten.")
        print(f"\nKette gesamt : {result['chain_total']:.4f} USDC")
        print(f"pool_spend   : {result['booked_total']:.4f} USDC")
        print(f"Differenz    : {result['difference']:+.4f} USDC")
        # The sign says which side is ahead, and only one direction is an
        # incident. A payment made minutes before this runs is booked before
        # the block explorer has indexed it; treating that as a discrepancy
        # would raise an alarm every time somebody spends on a Sunday morning.
        if result["difference"] > 0.0001:
            print("               die Kette liegt vorn — eine Ausgabe wurde nicht gebucht. Das ist der Alarm.")
        elif result["difference"] < -0.0001:
            print("               das Buch liegt vorn — vermutlich eine Zahlung, die der Explorer noch nicht")
            print("               indexiert hat. Beim naechsten Lauf pruefen, bevor daraus ein Befund wird.")
        if result["unbooked"]:
            print(f"\n{len(result['unbooked'])} Abfluesse ohne passenden Hash in pool_spend:")
            for r in result["unbooked"]:
                print(f"  {r['ts']}  {r['usdc']:>8.4f}  -> {r['to'][:14]}…  {r['tx'][:22]}…  [{r['pool']}]")
            print("\nEin Relay-Hash in pool_spend zaehlt hier als 'ohne Treffer', obwohl die")
            print("Buchung stimmt — die Differenz oben ist das belastbare Signal, nicht die Liste.")

    # The difference is the alarm. An unmatched hash alone is not, for the
    # relay reason in the module docstring.
    # Only the chain running ahead is an incident: that is money out with no
    # record. The book running ahead resolves itself once the explorer catches
    # up, so it reports without failing.
    return 1 if result["difference"] > 0.0001 else 0


if __name__ == "__main__":
    sys.exit(main())
