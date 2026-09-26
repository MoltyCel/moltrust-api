#!/usr/bin/env python3
"""Recompute the TSK-J3R0MDGA winner list from the live state and report it.

Round 1 was scored by hand and the sighting cost more than the bounty. This
draws the same list from the database and the submissions every time it runs,
so the list presented on Monday is Monday's list and not a Friday copy someone
forgot to refresh. Nothing is accepted and nothing is paid; the acceptance stays
a human step, and the command is written out only when --emit-command is given.

Parameters are fixed here rather than passed in, because they were decided once
and every run has to answer the same question:

  include_a2a        yes — thirteen agents did every step under platform='a2a'
  cap per address    two paid submissions, earliest first
  no submission      no payment; the task text pays submissions, and those who
                     bound a wallet without submitting stay eligible for stage 2
  shares             base bps for every paid submission, the remainder one bps
                     at a time to the earliest wallet_bound_at, summing to 10000
  presentation       one entry per worker address, shares added. Nothing rejects
                     a repeated address client-side and nothing promises the
                     contract accepts one; the money step is the wrong place to
                     find out, and merging pays the same operator the same amount.

    python3 scripts/bounty_r2_payout_list.py
    python3 scripts/bounty_r2_payout_list.py --emit-command /tmp/accept_cmd.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import defaultdict

TASK = "0xcae1c4c262e520898f96f2c36deea24ed529c58946904a721922a083a8ecff7b"
REF = "TSK-J3R0MDGA"
ROUND_START = "2026-09-23 15:14"
CAP_PER_ADDRESS = 2
# Confirmed unchanged on 2026-09-26. The task drew 125 submissions against 100
# paid slots, so the cap binds. Raising it to 125 would cost 1.35 USDC more in
# escrow and would mostly pay agents that already registered in round 1 — a
# fairness question, and no growth. Whoever changes this number says so here.
WINNER_SLOTS = 100
GROSS_USDC = 5.41
FEE_BPS = 750
DEADLINE = "2026-09-29"
CACHE = os.path.join(tempfile.gettempdir(), "r2_deliverables.json")

DID_RE = re.compile(r"^did:moltrust:[0-9a-f]{16}$")
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def psql(sql, **params):
    """Run one query. Values go in as psql variables, read back with :'name'.

    psql quotes them itself, so no value is ever pasted into the statement.
    The statement arrives on stdin because -c skips variable substitution.
    """
    binds = []
    for name, value in params.items():
        binds += ["-v", f"{name}={value}"]
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack", "-X", "-A",
         "-F", "\t", "-t", *binds, "-f", "-"], input=sql,
        capture_output=True, text=True, timeout=180)
    if out.returncode:
        raise SystemExit(f"psql: {out.stderr[:300]}")
    return [l.split("\t") for l in out.stdout.strip().split("\n") if l.strip()]


def cli(*args, timeout=180):
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.npm-global/bin") + ":" + env.get("PATH", "")
    out = subprocess.run(["taskmarket", *args], capture_output=True, text=True,
                         timeout=timeout, env=env)
    try:
        return json.loads(out.stdout)
    except Exception:
        return {}


def telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID_ALERTS") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("(kein Telegram-Token, nur Konsole)")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(  # noqa: S310  # nosec B310 - api.telegram.org, literal
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                   data=data), timeout=25).read()
    except Exception as exc:  # noqa: BLE001 - a failed report must not fail the run
        print(f"Telegram fehlgeschlagen: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-command", metavar="PATH",
                    help="Write the accept-submissions line. Omit it and nothing "
                         "executable is left lying around.")
    args = ap.parse_args()

    task = (cli("task", "get", TASK) or {}).get("data") or {}
    status, awards = task.get("status"), task.get("awardCount")
    print(f"{REF}: status {status}, awardCount {awards}, "
          f"{task.get('submissionCount')} Einreichungen, Ablauf {task.get('expiryTime')}")
    if awards:
        msg = (f"MolTrust — {REF} ist bereits vergeben ({awards} Awards).\n"
               "Es wurde nichts neu gerechnet.")
        print(msg)
        telegram(msg)
        return 0

    # Stage 1, live.
    rows = psql("""
        SELECT a.did, lower(a.wallet_address), a.platform, a.wallet_bound_at::text
          FROM agents a
          JOIN api_keys k ON k.owner_did = a.did AND k.signup_method = 'did_signature'
         WHERE a.wallet_bound_at >= :'round_start'::timestamptz
           AND a.revoked_at IS NULL AND lower(a.wallet_chain) = 'base'
           AND a.platform IN ('taskmarket','a2a')""", round_start=ROUND_START)
    eligible = {did: {"wallet": w, "platform": p, "bound": b} for did, w, p, b in rows}

    subs = (cli("task", "submissions", TASK) or {}).get("data") or []
    if isinstance(subs, dict):
        subs = subs.get("submissions") or []
    print(f"Stufe 1 erfüllt: {len(eligible)} · Einreichungen geladen: {len(subs)}")

    bodies = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    fetched = 0
    for s in subs:
        if s["id"] in bodies:
            continue
        r = subprocess.run(
            ["taskmarket", "task", "download", TASK, "--submission", s["id"]],
            capture_output=True, text=True, timeout=90,
            env={**os.environ,
                 "PATH": os.path.expanduser("~/.npm-global/bin") + ":" + os.environ.get("PATH", "")})
        bodies[s["id"]] = r.stdout.strip()
        fetched += 1
        time.sleep(0.3)
    json.dump(bodies, open(CACHE, "w"))
    print(f"Deliverables: {fetched} neu geladen, {len(bodies)} im Cache")

    # One paid entry per DID: its earliest valid submission.
    entries = []
    for s in sorted(subs, key=lambda x: x.get("submittedAt") or ""):
        body = bodies.get(s["id"], "")
        obj = None
        try:
            obj = json.loads(body)
        except Exception:
            m = re.search(r"\{.*\}", body, re.S)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    obj = None
        if not isinstance(obj, dict):
            continue
        did, wal = obj.get("did"), obj.get("wallet_address")
        if not (isinstance(did, str) and DID_RE.match(did)):
            continue
        if not (isinstance(wal, str) and ADDR_RE.match(wal)):
            continue
        e = eligible.get(did)
        if e is None or e["wallet"] != wal.lower():
            continue
        entries.append({"did": did, "ref": s["referenceCode"], "sub_id": s["id"],
                        "addr": s["workerAddress"], "addr_lc": s["workerAddress"].lower(),
                        "submitted_at": s.get("submittedAt") or "",
                        "platform": e["platform"], "bound": e["bound"]})

    best = {}
    for e in entries:
        best.setdefault(e["did"], e)
    per_did = sorted(best.values(), key=lambda x: x["submitted_at"])
    no_sub = sorted(set(eligible) - set(best))

    by_addr, kept, cut = defaultdict(list), [], []
    for e in per_did:
        if len(by_addr[e["addr_lc"]]) < CAP_PER_ADDRESS:
            by_addr[e["addr_lc"]].append(e)
            kept.append(e)
        else:
            cut.append(e)

    if not kept:
        msg = f"MolTrust — {REF}: keine zahlbaren Einreichungen. Nichts vorgelegt."
        print(msg)
        telegram(msg)
        return 1

    order = sorted(kept, key=lambda x: (x["bound"], x["submitted_at"]))
    base = 10000 // len(order)
    rest = 10000 - base * len(order)
    for i, e in enumerate(order):
        e["bps"] = base + (1 if i < rest else 0)
    assert sum(e["bps"] for e in order) == 10000

    merged = {}
    for e in order:
        m = merged.setdefault(e["addr_lc"], {"addr": e["addr"], "bps": 0, "slots": 0,
                                             "dids": [], "subs": [], "bound": e["bound"]})
        m["bps"] += e["bps"]
        m["slots"] += 1
        m["dids"].append(e["did"])
        m["subs"].append(e["ref"])
        m["bound"] = min(m["bound"], e["bound"])
    payees = sorted(merged.values(), key=lambda x: x["bound"])
    assert sum(m["bps"] for m in payees) == 10000

    net = lambda b: GROSS_USDC * b / 10000 * (1 - FEE_BPS / 10000)
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "task": TASK, "ref": REF, "stage1": len(eligible),
           "no_submission": no_sub, "slots": len(order), "cut_by_cap": len(cut),
           "base_bps": base, "remainder": rest, "payees": payees}
    path = os.path.expanduser(f"~/r2-winners-{time.strftime('%Y%m%d')}.json")
    json.dump(out, open(path, "w"), indent=1)

    if args.emit_command:
        with open(args.emit_command, "w") as f:
            f.write(f"taskmarket task accept-submissions {TASK} \\\n  "
                    + " \\\n  ".join(f"--winner {m['addr']}:{m['bps']}" for m in payees) + "\n")
        print(f"Befehl geschrieben: {args.emit_command}")

    over = max(len(subs) - WINNER_SLOTS, 0)
    msg = (f"MolTrust — {REF}, Gewinnerliste zur Freigabe\n\n"
           f"Stufe 1 erfuellt      {len(eligible)}\n"
           f"ohne Einreichung      {len(no_sub)}  (nicht bezahlt, Stufe-2-faehig)\n"
           f"bezahlte Einreichungen {len(order)}\n"
           f"Eintraege im Aufruf   {len(payees)}\n"
           f"vom Deckel gekappt    {len(cut)}\n"
           f"Gewinnerplaetze       {WINNER_SLOTS}  (unveraendert, Beschluss 26.09.)"
           + (f" — {over} Einreichungen darueber\n" if over else "\n") + "\n"
           f"Basis {base} bps, +1 auf die {rest} fruehesten, Summe 10000.\n"
           f"netto je Adresse {net(base):.6f}-{net(base+1):.6f} USDC, "
           f"Summe {sum(net(m['bps']) for m in payees):.4f}.\n\n"
           f"Liste: {path}\n"
           f"Annahme muss bis {DEADLINE} erfolgt sein. Nichts ausgefuehrt.")
    print("\n" + msg)
    telegram(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
