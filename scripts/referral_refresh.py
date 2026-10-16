#!/usr/bin/env python3
"""Re-issue the ReferralCredential of every referrer whose numbers moved (ops G2).

Two numbers stand in the credential: how many agents a referrer brought, and
how many of those activated. The first changes on the registration itself and
is re-issued there. The second changes when a referred agent makes a call of its
own, which is nothing the registration path ever sees — so it is read here, once
a day, from the same rows the registry proof reads.

At most one credential a day per referrer, decided by app.referral.should_issue:
a run that finds nothing changed issues nothing and says so. The anchor is not
written here either; scripts/anchor_iprs.sh batches every unanchored credential
into one Base transaction every two hours.

    python3 scripts/referral_refresh.py             # issue what is due
    python3 scripts/referral_refresh.py --dry-run   # decide and report, issue nothing
"""
import argparse
import asyncio
import datetime
import json
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import referral  # noqa: E402


async def referrers(conn) -> list[str]:
    """Every DID that is named as a referrer, counted or not.

    Named and not counted is on purpose: a referrer whose only referral stopped
    counting — revoked, or the other half of a mutual pair appeared — has a
    credential that is now too high, and leaving it out here would be how it
    stays too high.
    """
    rows = await conn.fetch(
        "SELECT DISTINCT referrer_did FROM agent_referrals ORDER BY referrer_did")
    return [r["referrer_did"] for r in rows]


async def run(dry_run: bool) -> dict:
    conn = await asyncpg.connect(host="localhost", user="moltstack",
                                 database=os.getenv("DB_NAME", "moltstack"))
    now = datetime.datetime.now(datetime.timezone.utc)
    report = {"as_of": now.replace(microsecond=0).isoformat(),
              "dry_run": dry_run, "referrers": 0, "issued": [], "unchanged": []}
    try:
        for did in await referrers(conn):
            report["referrers"] += 1
            figures = await referral.counts_for(conn, did)
            last = await referral.last_credential(conn, did)
            due = referral.should_issue(figures, last, now)
            entry = {"did": did, **figures,
                     "last": (last or {}).get("figures"),
                     "last_issued_at": (last or {}).get("issued_at").isoformat()
                     if (last or {}).get("issued_at") else None}
            if not due:
                report["unchanged"].append(entry)
                continue
            if dry_run:
                report["issued"].append({**entry, "credential_id": None})
                continue
            result = await referral.refresh(conn, did, now)
            report["issued"].append({**entry,
                                     "credential_id": result.get("credential_id")})
    finally:
        await conn.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="decide and report, write nothing")
    args = ap.parse_args()
    report = asyncio.run(run(args.dry_run))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
