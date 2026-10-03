#!/usr/bin/env python3
"""Qualify submissions on every open task and present the winners when ten qualify.

Replaces scripts/series_watch.py, which knew one task id. Five tasks are open
now — the series part and four stage-1 tasks — and a watcher that covers one of
five is the shape that lets four go unmeasured while reporting green on the
fifth.

Each task declares its own profile: what qualification means, how many slots,
and how many paid submissions one worker address may hold. Counting is per
task, rejections are reported per task with their reason, and the one-shot
alert at ten qualified fires per task.

Nothing is ever accepted here. The list goes up for approval and waits.

  * stage1  — the three calls of the stage-1 text: the DID resolves, an API key
              is bound to it, a wallet is bound on Base by signature, and the
              submitted did and wallet_address match what the database holds.
              One paid submission per worker address per task.
  * series  — additionally an anchored TrackRecordCredential and the two
              challenge figures the gate actually quotes. Two per address.

    python3 scripts/task_watch.py                 # every open task
    python3 scripts/task_watch.py --ref TSK-E49N4V7T
    python3 scripts/task_watch.py --always        # report even when short
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
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import notify  # noqa: E402

FEE_BPS = 750
STATE = os.path.expanduser("~/.task_watch.json")
OUTDIR = os.path.expanduser("~/task-winners")

# The open tasks and what qualification means on each. A task that closes stays
# listed: task_watch reads its state from the market and says "already awarded"
# or "expired" rather than going quiet about it.
TASKS = [
    {"ref": "TSK-E49N4V7T", "profile": "series", "slots": 10, "cap": 2,
     "gross": 0.541, "label": "Serie Teil 1/10",
     "id": "0x17ecab6a82dd441582d0de2bad96efa099b09ad0a5678738f9ba872c4270b0fd",
     "expect": {"challenge_amount": "40000", "challenge_without_headers": "50000"}},
    {"ref": "STUFE1-1", "profile": "stage1", "slots": 10, "cap": 1,
     "gross": 0.541, "label": "Stufe 1, Teil 1 von 4",
     "id": "0x61dbbb1dec8a0d605c88e56bd67b179663cc918dcd6c00fca86f1d8a1cc19f0e"},
    {"ref": "STUFE1-2", "profile": "stage1", "slots": 10, "cap": 1,
     "gross": 0.541, "label": "Stufe 1, Teil 2 von 4",
     "id": "0x26f27926775e0abf9d29b419098769533e45303f6ecae3b5b2606da6664b544d"},
    {"ref": "STUFE1-3", "profile": "stage1", "slots": 10, "cap": 1,
     "gross": 0.541, "label": "Stufe 1, Teil 3 von 4",
     "id": "0x65f00c65c22a0c557cc249fa86df1161d025b189e23639c968208a42ffe05540"},
    {"ref": "STUFE1-4", "profile": "stage1", "slots": 10, "cap": 1,
     "gross": 0.541, "label": "Stufe 1, Teil 4 von 4",
     "id": "0x1c948cb819fb31d151d8ff5a95ae98e9cc7a7cc547386f3360d4cc50ece2bc1d"},
]

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


def eligible_stage1():
    """DID resolvable, API key bound, wallet bound on Base by signature."""
    rows = psql("""
        SELECT a.did, lower(a.wallet_address)
          FROM agents a
         WHERE a.revoked_at IS NULL
           AND lower(a.wallet_chain) = 'base'
           AND a.wallet_address IS NOT NULL
           AND a.wallet_signature IS NOT NULL
           AND EXISTS (SELECT 1 FROM api_keys k
                        WHERE k.owner_did = a.did AND k.active)""")
    return {d: w for d, w in rows}


def eligible_series():
    """Stage 1, plus an anchored TrackRecordCredential."""
    rows = psql("""
        SELECT a.did, lower(a.wallet_address)
          FROM agents a
         WHERE a.revoked_at IS NULL
           AND lower(a.wallet_chain) = 'base'
           AND a.wallet_address IS NOT NULL
           AND EXISTS (SELECT 1 FROM credentials c
                         JOIN credential_anchors k ON k.credential_id = c.id
                        WHERE c.subject_did = a.did AND NOT c.revoked
                          AND c.credential_type = 'TrackRecordCredential')""")
    return {d: w for d, w in rows}


def deliverables(task_id, subs):
    """Submission bodies, cached per task so a rerun costs no downloads."""
    cache = os.path.join(tempfile.gettempdir(), f"tw_{task_id[:18]}.json")
    bodies = json.load(open(cache)) if os.path.exists(cache) else {}
    for s in subs:
        if bodies.get(s["id"], "").strip():
            continue
        r = subprocess.run(
            ["taskmarket", "task", "download", task_id, "--submission", s["id"]],
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "PATH": os.path.expanduser("~/.npm-global/bin")
                 + ":" + os.environ.get("PATH", "")})
        bodies[s["id"]] = r.stdout.strip()
        time.sleep(0.25)
    json.dump(bodies, open(cache, "w"))
    return bodies


def parse_body(body):
    try:
        return json.loads(body)
    except ValueError:
        m = re.search(r"\{.*\}", body, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None


def qualify(spec, subs, bodies, eligible):
    """(kept, cut_by_cap, reasons) for one task, earliest submission first."""
    reasons, entries = defaultdict(int), []
    for s in sorted(subs, key=lambda x: x.get("submittedAt") or ""):
        obj = parse_body(bodies.get(s["id"], ""))
        if not isinstance(obj, dict):
            reasons["kein lesbares JSON"] += 1
            continue
        did = obj.get("did")
        if not (isinstance(did, str) and DID_RE.match(did)):
            reasons["keine gueltige DID im Deliverable"] += 1
            continue
        if did not in eligible:
            reasons["DID erfuellt die Annahmekriterien nicht"] += 1
            continue
        # The task asks for the wallet address as well, and it has to be the
        # one the database holds. A submission that names someone else's wallet
        # is the one thing the signature binding exists to prevent.
        claimed = (obj.get("wallet_address") or "").lower()
        if spec["profile"] == "stage1":
            if not ADDR_RE.match(claimed):
                reasons["keine gueltige wallet_address im Deliverable"] += 1
                continue
            if claimed != eligible[did]:
                reasons["wallet_address weicht von der gebundenen ab"] += 1
                continue
        for field, want in (spec.get("expect") or {}).items():
            if str(obj.get(field)) != want:
                reasons[f"{field} != {want}"] += 1
                break
        else:
            if not ADDR_RE.match(s.get("workerAddress") or ""):
                reasons["Worker-Adresse unbrauchbar"] += 1
                continue
            entries.append({"did": did, "ref": s.get("referenceCode"),
                            "addr": s["workerAddress"],
                            "addr_lc": s["workerAddress"].lower(),
                            "at": s.get("submittedAt") or ""})

    best = {}
    for e in entries:
        best.setdefault(e["did"], e)

    by_addr, kept, cut = defaultdict(list), [], 0
    for e in sorted(best.values(), key=lambda x: x["at"]):
        if len(by_addr[e["addr_lc"]]) < spec["cap"]:
            by_addr[e["addr_lc"]].append(e)
            kept.append(e)
        else:
            cut += 1
    return kept, cut, reasons


def payout_list(winners, slots):
    bps = 10000 // slots
    merged = {}
    for w in winners:
        m = merged.setdefault(w["addr_lc"],
                              {"addr": w["addr"], "bps": 0, "slots": 0, "at": w["at"]})
        m["bps"] += bps
        m["slots"] += 1
        m["at"] = min(m["at"], w["at"])
    return sorted(merged.values(), key=lambda x: x["at"]), bps


def report(spec, task, kept, cut, reasons):
    slots, gross = spec["slots"], spec["gross"]
    ready = len(kept) >= slots
    winners = kept[:slots]
    payees, bps = payout_list(winners, slots)
    net = gross * (1 - FEE_BPS / 10000)

    body = (f"MolTrust — {spec['ref']}, {spec['label']}\n\n"
            f"Einreichungen      {task.get('submissionCount')}\n"
            f"qualifiziert       {len(kept)} von {slots} benoetigt\n"
            f"vom Adressdeckel   {cut}  (max {spec['cap']} je Adresse)\n"
            f"Ablauf             {task.get('expiryTime')}\n")
    if reasons:
        body += "\nAbgewiesen:\n" + "\n".join(
            f"  {n:>3}  {r}" for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])) + "\n"
    if ready:
        total = sum(p["bps"] for p in payees)
        if total != 10000:
            body += (f"\nFEHLER: bps-Summe {total}, erwartet 10000 — keine Liste.\n")
            return body, False, payees
        body += (f"\nZEHN QUALIFIZIERTE — Liste zur Freigabe:\n"
                 f"  Slots {slots} x {bps} bps, Summe 10000\n"
                 f"  Eintraege im Aufruf {len(payees)}\n"
                 f"  netto je Slot {net/slots:.6f} USDC, Summe {net:.4f}\n\n")
        for p in payees:
            extra = f"  ({p['slots']} Slots)" if p["slots"] > 1 else ""
            body += f"  {p['addr']}  {p['bps']} bps{extra}\n"
        body += "\nNichts angenommen. Freigabe von Lars, dann ein Aufruf.\n"
    return body, ready, payees


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--always", action="store_true")
    ap.add_argument("--ref", help="nur diese Task-Referenz")
    args = ap.parse_args()

    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except (OSError, ValueError):
            state = {}

    pool = [t for t in TASKS if not args.ref or t["ref"] == args.ref]
    if args.ref and not pool:
        print(f"Keine Task-Referenz {args.ref} im Register.")
        return 2

    cache_eligible, unreadable, lines = {}, [], []
    os.makedirs(OUTDIR, exist_ok=True)

    for spec in pool:
        task = (cli("task", "get", spec["id"]) or {}).get("data") or {}
        if not task:
            # Not measurable is not zero. It is said out loud and the exit code
            # carries it.
            unreadable.append(f"{spec['ref']}: Task nicht abfragbar")
            lines.append(f"{spec['ref']}: NICHT ABFRAGBAR — keine Aussage.")
            continue
        if task.get("awardCount"):
            lines.append(f"{spec['ref']}: bereits vergeben "
                         f"({task['awardCount']} Awards).")
            continue

        subs = (cli("task", "submissions", spec["id"]) or {}).get("data") or []
        if isinstance(subs, dict):
            subs = subs.get("submissions") or []

        if spec["profile"] not in cache_eligible:
            cache_eligible[spec["profile"]] = (
                eligible_series() if spec["profile"] == "series" else eligible_stage1())

        bodies = deliverables(spec["id"], subs)
        kept, cut, reasons = qualify(spec, subs, bodies,
                                     cache_eligible[spec["profile"]])
        body, ready, payees = report(spec, task, kept, cut, reasons)
        lines.append(body)

        key = spec["ref"]
        before = state.get(key) or {}
        if ready:
            json.dump({"task": spec["id"], "ref": key, "slots": spec["slots"],
                       "payees": payees, "cut_by_cap": cut},
                      open(os.path.join(OUTDIR, f"{key}.json"), "w"), indent=1)
        if ready and not before.get("alerted"):
            notify.send_telegram("MolTrust — " + body, channel=notify.ALERTS)
            before["alerted"] = True
        elif args.always or before.get("qualified") != len(kept):
            notify.send_telegram("MolTrust — " + body, channel=notify.STATS)
        state[key] = {**before, "qualified": len(kept),
                      "submissions": task.get("submissionCount")}

    out = "\n".join(lines)
    print(out)
    if unreadable:
        print("\nNicht gemessen:\n  " + "\n  ".join(unreadable))
        notify.send_telegram("MolTrust — Qualifizierer konnte nicht messen:\n  "
                             + "\n  ".join(unreadable), channel=notify.ALERTS)
    json.dump(state, open(STATE, "w"), indent=1)
    return 2 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
