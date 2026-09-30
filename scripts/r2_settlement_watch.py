#!/usr/bin/env python3
"""Watch TSK-J3R0MDGA for anything that makes a payout possible again.

The acceptance failed on 2026-09-28 with an undecodable revert (0xc6671ec1)
inside their relayer, reported as daydreamsai/skills-market#68. Nothing on our
side can retry it: acceptSubmissions is callable only through their forwarder,
and eth_call answers NotTrustedForwarder to everyone else.

So this watches rather than acts. Three things can change:

  * the support issue gets an answer
  * the task passes its expiry and offers the requester an action. Per
    skills-market#61 an expired task sits in `awaiting_settlement` until the
    requester moves it — there is no timer and no automatic refund, so the
    escrow stays put until someone looks.
  * USDC lands back on the requester wallet

It reports and does not decide. Nothing here accepts, refunds or pays, and
there is no retry: a second acceptance attempt is a human call, once.

    python3 scripts/r2_settlement_watch.py            # one pass, report on change
    python3 scripts/r2_settlement_watch.py --always   # report even when nothing moved
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notify  # noqa: E402 - path set before the import

TASK = "0xcae1c4c262e520898f96f2c36deea24ed529c58946904a721922a083a8ecff7b"
REF = "TSK-J3R0MDGA"
WALLET = "0xa175d51bfe0170738720DAAEc627A84d44dc9Eb9"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
ISSUE = "daydreamsai/skills-market#68"
ISSUE_API = "https://api.github.com/repos/daydreamsai/skills-market/issues/68"
EXPIRY = "2026-09-30T15:14:04Z"
STATE = os.path.expanduser("~/.r2_settlement_watch.json")


def rpc(method, params):
    url = os.environ.get("BASE_RPC", "https://mainnet.base.org")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urllib.request.Request(url, data=body.encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310  # nosec B310
        return json.loads(r.read()).get("result")


def task_state():
    """The task as their API reports it, with `reachable` kept separate.

    The first version returned {} for a parse failure and for a missing task
    alike, so when their API answered 502 on 2026-09-30 the watcher reported
    "status: open -> None" and read like the task had been closed. An outage and
    a state change are different events and a watcher that conflates them costs
    somebody an hour on a deadline day.
    """
    env = dict(os.environ)
    env["PATH"] = os.path.expanduser("~/.npm-global/bin") + ":" + env.get("PATH", "")
    try:
        out = subprocess.run(["taskmarket", "task", "get", TASK],
                             capture_output=True, text=True, timeout=120, env=env)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"reachable": False, "why": f"CLI: {type(exc).__name__}"}
    try:
        parsed = json.loads(out.stdout)
    except ValueError:
        return {"reachable": False, "why": f"keine JSON-Antwort: {out.stdout[:80]!r}"}
    if parsed.get("ok") is False or "error" in parsed:
        # Their API answering with an error is their outage, not our task.
        err = str(parsed.get("error") or "")[:120]
        return {"reachable": False, "why": f"API-Fehler (status {parsed.get('status')}): {err}"}
    d = parsed.get("data")
    if not d:
        return {"reachable": True, "found": False,
                "why": "API antwortet, kennt die Task aber nicht"}
    return {"reachable": True, "found": True,
            "status": d.get("status"), "phase": d.get("phase"),
            "awards": d.get("awardCount"), "submissions": d.get("submissionCount"),
            "pending": [p.get("role") for p in (d.get("pendingActions") or [])]}


def issue_replies():
    """Comment count on the support issue. Unauthenticated: 60/h shared, and
    this runs hourly at most, so it stays inside the budget."""
    try:
        req = urllib.request.Request(ISSUE_API, headers={"User-Agent": "MolTrust/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:  # noqa: S310  # nosec B310
            d = json.loads(r.read())
        return {"comments": d.get("comments"), "state": d.get("state")}
    except Exception:
        return {"comments": None, "state": None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--always", action="store_true",
                    help="report even when nothing changed")
    args = ap.parse_args()

    usdc = int(rpc("eth_call", [{"to": USDC, "data": "0x70a08231"
                                 + WALLET[2:].lower().rjust(64, "0")}, "latest"]), 16)
    now = {"usdc": usdc, "task": task_state(), "issue": issue_replies()}

    before = {}
    if os.path.exists(STATE):
        try:
            before = json.load(open(STATE))
        except (OSError, ValueError):
            before = {}

    changes = []
    if before:
        if before.get("usdc") != usdc:
            d = (usdc - before["usdc"]) / 1e6
            changes.append(f"USDC auf {WALLET[:10]}…: {before['usdc']/1e6:.6f} -> "
                           f"{usdc/1e6:.6f} ({d:+.6f})")
        bt, nt = before.get("task") or {}, now["task"]
        # Only compare the task's own fields when both readings actually saw it.
        # An outage is reported as an outage and compared against nothing.
        if nt.get("reachable") and nt.get("found") and bt.get("reachable") and bt.get("found"):
            for k in ("status", "phase", "awards"):
                if bt.get(k) != nt.get(k):
                    changes.append(f"Task {k}: {bt.get(k)} -> {nt.get(k)}")
        elif nt.get("reachable") is False and bt.get("reachable") is not False:
            changes.append(f"Ihre API nicht erreichbar: {nt.get('why')}")
        elif nt.get("reachable") and not nt.get("found") and bt.get("found"):
            changes.append("Ihre API antwortet, kennt die Task aber nicht mehr")
        elif nt.get("reachable") and nt.get("found") and bt.get("reachable") is False:
            changes.append("Ihre API antwortet wieder")
        if now["task"].get("pending") and not (before.get("task") or {}).get("pending"):
            changes.append(f"pendingActions neu: {now['task']['pending']}")
        bi, ni = before.get("issue") or {}, now["issue"]
        if bi.get("comments") is not None and ni.get("comments") is not None \
                and ni["comments"] > bi["comments"]:
            changes.append(f"{ISSUE}: {ni['comments'] - bi['comments']} neue Antwort(en)")

    expired = datetime.now(timezone.utc) > datetime.fromisoformat(EXPIRY.replace("Z", "+00:00"))
    t = now["task"]
    if not t.get("reachable"):
        line = f"Task      NICHT ABFRAGBAR — {t.get('why')}\n" \
               f"          (Aussage ueber die Task: keine. On-chain siehe Escrow.)\n"
    elif not t.get("found"):
        line = "Task      API antwortet, kennt die Task nicht\n"
    else:
        line = (f"Task      status {t.get('status')} · phase {t.get('phase')} · "
                f"awards {t.get('awards')}\n"
                f"pending   {t.get('pending') or 'keine'}\n")
    body = (f"MolTrust — {REF}, Beobachtung\n\n" + line
            + f"Escrow    {usdc/1e6:.6f} USDC auf {WALLET[:10]}…\n"
            f"Support   {ISSUE}, {now['issue'].get('comments')} Kommentare, "
            f"{now['issue'].get('state')}\n"
            f"Ablauf    {EXPIRY} — {'ueberschritten' if expired else 'laeuft noch'}\n")
    if changes:
        body += "\nVERAENDERT:\n" + "\n".join(f"  - {c}" for c in changes) + "\n"
        body += ("\nNichts wurde ausgefuehrt. Ein zweiter Annahmeversuch ist eine\n"
                 "Freigabe von Lars, einmalig.\n")

    print(body)
    if changes or args.always:
        notify.send_telegram(body, channel=notify.ALERTS if changes else notify.STATS)
    json.dump({**now, "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
              open(STATE, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
