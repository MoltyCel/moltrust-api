#!/usr/bin/env python3
"""Qualify submissions on the series task and present ten winners when there are ten.

The point of this part is the path proof: an acceptance with ten recipients is
the largest the contract has ever paid, measured on chain over sixty days, and
we have never got one through. So the list goes up for approval the moment ten
qualify rather than at expiry.

Qualification follows the task text and nothing else:

  * the submission parses and carries did plus both challenge figures
  * the DID is registered with us, not revoked, with a wallet bound on Base
  * the DID holds an anchored TrackRecordCredential, which is what the task
    asked the worker to obtain
  * the two figures match what the gate actually quotes
  * at most two paid submissions per worker address, earliest first

Ten slots at 1000 bps each, summing to 10000. Nothing is accepted here.

    python3 scripts/series_watch.py            # report, alert once ten qualify
    python3 scripts/series_watch.py --always   # report even when short
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import notify  # noqa: E402

TASK = "0x17ecab6a82dd441582d0de2bad96efa099b09ad0a5678738f9ba872c4270b0fd"
REF = "TSK-E49N4V7T"
SLOTS = 10
BPS = 10000 // SLOTS
CAP_PER_ADDRESS = 2
GROSS = 0.541
FEE_BPS = 750
EXPECT_WITH = "40000"
EXPECT_WITHOUT = "50000"
CACHE = "/tmp/series1_deliverables.json"
STATE = os.path.expanduser("~/.series_watch.json")
OUT = os.path.expanduser("~/series1-winners.json")

DID_RE = re.compile(r"^did:moltrust:[0-9a-f]{16}$")
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def cli(*args, timeout=180):
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
        capture_output=True, text=True, timeout=180)
    if out.returncode:
        raise SystemExit(f"psql: {out.stderr[:200]}")
    return [ln.split("\t") for ln in out.stdout.strip().split("\n") if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--always", action="store_true")
    args = ap.parse_args()

    task = (cli("task", "get", TASK) or {}).get("data") or {}
    if not task:
        print(f"{REF}: Task nicht abfragbar — keine Aussage.")
        return 2
    if task.get("awardCount"):
        print(f"{REF}: bereits vergeben ({task['awardCount']} Awards).")
        return 0

    subs = (cli("task", "submissions", TASK) or {}).get("data") or []
    if isinstance(subs, dict):
        subs = subs.get("submissions") or []

    bodies = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    for s in subs:
        if bodies.get(s["id"], "").strip():
            continue
        r = subprocess.run(
            ["taskmarket", "task", "download", TASK, "--submission", s["id"]],
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "PATH": os.path.expanduser("~/.npm-global/bin")
                 + ":" + os.environ.get("PATH", "")})
        bodies[s["id"]] = r.stdout.strip()
        time.sleep(0.25)
    json.dump(bodies, open(CACHE, "w"))

    # who is eligible at all: registered, not revoked, wallet on Base, anchored
    # TrackRecordCredential — the thing this task asked them to get.
    rows = psql("""
        SELECT a.did, lower(a.wallet_address)
          FROM agents a
         WHERE a.revoked_at IS NULL AND lower(a.wallet_chain) = 'base'
           AND a.wallet_address IS NOT NULL
           AND EXISTS (SELECT 1 FROM credentials c
                         JOIN credential_anchors k ON k.credential_id = c.id
                        WHERE c.subject_did = a.did AND NOT c.revoked
                          AND c.credential_type = 'TrackRecordCredential')""")
    eligible = {d: w for d, w in rows}

    reasons = defaultdict(int)
    entries = []
    for s in sorted(subs, key=lambda x: x.get("submittedAt") or ""):
        obj = None
        body = bodies.get(s["id"], "")
        try:
            obj = json.loads(body)
        except ValueError:
            m = re.search(r"\{.*\}", body, re.S)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except ValueError:
                    obj = None
        if not isinstance(obj, dict):
            reasons["kein lesbares JSON"] += 1
            continue
        did = obj.get("did")
        if not (isinstance(did, str) and DID_RE.match(did)):
            reasons["keine gueltige DID im Deliverable"] += 1
            continue
        if did not in eligible:
            reasons["DID ohne verankerte TrackRecordCredential"] += 1
            continue
        if str(obj.get("challenge_amount")) != EXPECT_WITH:
            reasons[f"challenge_amount != {EXPECT_WITH}"] += 1
            continue
        if str(obj.get("challenge_without_headers")) != EXPECT_WITHOUT:
            reasons[f"challenge_without_headers != {EXPECT_WITHOUT}"] += 1
            continue
        if not ADDR_RE.match(s.get("workerAddress") or ""):
            reasons["Worker-Adresse unbrauchbar"] += 1
            continue
        entries.append({"did": did, "ref": s["referenceCode"],
                        "addr": s["workerAddress"], "addr_lc": s["workerAddress"].lower(),
                        "at": s.get("submittedAt") or ""})

    best = {}
    for e in entries:
        best.setdefault(e["did"], e)
    per_did = sorted(best.values(), key=lambda x: x["at"])

    by_addr, kept, cut = defaultdict(list), [], 0
    for e in per_did:
        if len(by_addr[e["addr_lc"]]) < CAP_PER_ADDRESS:
            by_addr[e["addr_lc"]].append(e)
            kept.append(e)
        else:
            cut += 1

    ready = len(kept) >= SLOTS
    winners = kept[:SLOTS]
    for w in winners:
        w["bps"] = BPS

    merged = {}
    for w in winners:
        m = merged.setdefault(w["addr_lc"], {"addr": w["addr"], "bps": 0, "slots": 0, "at": w["at"]})
        m["bps"] += w["bps"]
        m["slots"] += 1
        m["at"] = min(m["at"], w["at"])
    payees = sorted(merged.values(), key=lambda x: x["at"])

    net = GROSS * (1 - FEE_BPS / 10000)
    body = (f"MolTrust — {REF}, Serie Teil 1/10\n\n"
            f"Einreichungen      {task.get('submissionCount')}\n"
            f"qualifiziert       {len(kept)} von {SLOTS} benoetigt\n"
            f"vom Adressdeckel   {cut}\n"
            f"Ablauf             {task.get('expiryTime')}\n")
    if reasons:
        body += "\nAbgewiesen:\n" + "\n".join(f"  {n:>3}  {r}" for r, n in
                                              sorted(reasons.items(), key=lambda kv: -kv[1])) + "\n"
    if ready:
        assert sum(p["bps"] for p in payees) == 10000
        body += (f"\nZEHN QUALIFIZIERTE — Liste zur Freigabe:\n"
                 f"  Slots {SLOTS} x {BPS} bps, Summe 10000\n"
                 f"  Eintraege im Aufruf {len(payees)}\n"
                 f"  netto je Slot {net/SLOTS:.6f} USDC, Summe {net:.4f}\n\n")
        for p in payees:
            body += f"  {p['addr']}  {p['bps']} bps{'  (2 Slots)' if p['slots'] > 1 else ''}\n"
        body += "\nNichts angenommen. Freigabe von Lars, dann ein Aufruf.\n"
        json.dump({"task": TASK, "ref": REF, "slots": SLOTS, "payees": payees,
                   "cut_by_cap": cut}, open(OUT, "w"), indent=1)

    print(body)

    before = {}
    if os.path.exists(STATE):
        try:
            before = json.load(open(STATE))
        except (OSError, ValueError):
            before = {}
    changed = before.get("qualified") != len(kept)
    if ready and not before.get("alerted"):
        notify.send_telegram(body, channel=notify.ALERTS)
        before["alerted"] = True
    elif args.always or changed:
        notify.send_telegram(body, channel=notify.STATS)
    json.dump({**before, "qualified": len(kept), "submissions": task.get("submissionCount")},
              open(STATE, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
