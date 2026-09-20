#!/usr/bin/env python3
"""Daily count of what the taskmarket bounties actually produced.

Three numbers, and one of them is honestly missing.

  submissions  read from the task's submissionCount via the first-party CLI.
  registered   agents in our own database on platform 'taskmarket' since the
               bounties went live. This is the number the funnel is about: did
               posting work bring anyone to a registration.
  with a DID   still not available, and now for a sharper reason. The indexer
               (scripts/taskmarket_index.py) reads TaskSubmitted straight off
               Base, so the submission count and the worker wallets are exact.
               But `deliverable` is a bytes32 hash: the body is off-chain and
               neither the CLI nor the public API serves it. The DID a
               submission claims is not readable from anywhere we can reach.
               Reported as null rather than guessed.

The gap is deliberate: a count that silently substitutes "registered" for "with
a DID" would overstate on days when someone registers without submitting, and
understate on days when someone submits a DID they registered last week.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date

import asyncio
import asyncpg

TASKS = {
    "score": "0xea9b5bd5310567979355dcc4a14995a21769cc413c4f29a3b83b03429f461168",
    "verify": "0xbe177536acec7b15bed66e92738d29e70073f43e98b0c36cffcd8a383019b80c",
}
BOUNTY_EPOCH = date(2026, 9, 20)
CLI = os.path.expanduser("~/.npm-global/bin/taskmarket")


def submission_count(task_id: str) -> int | None:
    """The CLI's own count.

    Kept beside the on-chain figure rather than replaced by it: on 2026-09-20
    the CLI said 17 while the chain held 30, and a disagreement between the
    platform's number and the ledger's is worth seeing rather than smoothing.
    """
    try:
        out = subprocess.run([CLI, "task", "get", task_id], capture_output=True,
                             text=True, timeout=90)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)["data"].get("submissionCount")
    except Exception:
        return None


def onchain_counts() -> dict | None:
    """Submissions as Base recorded them."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run([sys.executable, os.path.join(here, "taskmarket_index.py")],
                             capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            return None
        d = json.loads(out.stdout)
        return {"total": d["submissions_total"],
                "distinct_workers": d["distinct_workers_total"],
                "by_task": {k: v["submissions"] for k, v in d["tasks"].items()}}
    except Exception:
        return None


async def main() -> int:
    counts = {name: submission_count(tid) for name, tid in TASKS.items()}
    total = sum(c for c in counts.values() if isinstance(c, int))
    unreadable = [n for n, c in counts.items() if c is None]

    conn = await asyncpg.connect(host="localhost", user="moltstack", database="moltstack")
    rows = await conn.fetch(
        """SELECT did, display_name, created_at
             FROM agents
            WHERE funnel_platform_bucket(platform) = 'taskmarket'
              AND created_at >= $1::date
              AND revoked_at IS NULL
            ORDER BY created_at""",
        BOUNTY_EPOCH,
    )
    # Did any of them get past registering?
    active = await conn.fetchval(
        """SELECT count(DISTINCT k.did) FROM usage_daily_keys k
             JOIN agents a ON a.did = k.did
            WHERE funnel_platform_bucket(a.platform) = 'taskmarket'
              AND k.day >= $1::date""",
        BOUNTY_EPOCH,
    )
    await conn.close()

    chain = onchain_counts()
    report = {
        "date": date.today().isoformat(),
        "submissions": (chain or {}).get("total", total),
        "submissions_onchain": chain,
        "submissions_per_cli": total,
        "submissions_by_task": counts,
        "submissions_unreadable": unreadable,
        "registered": len(rows),
        "registered_dids": [r["did"] for r in rows],
        "made_an_authenticated_call": active,
        "with_valid_did": None,
        "with_valid_did_note": "deliverable bodies live in the contract's submission "
                               "history; the CLI exposes no read command for them",
    }
    print(json.dumps(report, indent=1))

    shown = (chain or {}).get("total", total)
    line = (f"taskmarket: {shown} submissions / "
            f"{report['with_valid_did'] if report['with_valid_did'] is not None else '?'} mit DID / "
            f"{len(rows)} registriert")
    if active:
        line += f" ({active} davon mit Call)"
    print("\nTelegram-Zeile:", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
