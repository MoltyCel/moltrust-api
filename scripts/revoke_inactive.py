#!/usr/bin/env python3
"""Revoke DIDs that never made an authenticated call. Proposal — see
docs/decisions/ADR-inactive-did-revocation.md; not scheduled.

Deliberately narrow: only agents that were never active, never those that went
quiet. An agent with one call on day two and silence since has arrived; that is
a retention question, not a counting one.

    python3 scripts/revoke_inactive.py --dry-run     # list, change nothing
    python3 scripts/revoke_inactive.py               # revoke and report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import datetime as _dt

import asyncpg

INACTIVE_DAYS = 90
REASON = "inactive_90d"

# Caller identity has only been recorded since #342. Before that date
# usage_daily_keys is empty for everyone, so "no row" means "not measured", not
# "never called". A first dry run without this guard proposed 76 agents, nearly
# all of them from before the telemetry existed.
TELEMETRY_EPOCH = _dt.date(2026, 9, 14)


async def candidates(conn, days: int):
    return await conn.fetch(
        """SELECT a.did, a.display_name, a.platform, a.created_at,
                  (current_date - a.created_at::date) AS age_days
             FROM agents a
             LEFT JOIN usage_daily_keys k ON k.did = a.did
            WHERE a.revoked_at IS NULL
              AND a.created_at < now() - ($1 || ' days')::interval
              AND a.created_at >= $2
              AND k.did IS NULL
            GROUP BY a.did, a.display_name, a.platform, a.created_at
            ORDER BY a.created_at""",
        str(days), TELEMETRY_EPOCH,
    )


async def main(dry_run: bool, days: int) -> int:
    conn = await asyncpg.connect(host="localhost", user="moltstack", database="moltstack")
    rows = await candidates(conn, days)
    report = {
        "inactive_days": days,
        "telemetry_epoch": TELEMETRY_EPOCH.isoformat(),
        "note": "agents registered before the telemetry epoch are out of scope: "
                "no usage row means unmeasured, not inactive",
        "candidates": len(rows),
        "dry_run": dry_run,
        "by_platform": {},
        "dids": [],
    }
    for r in rows:
        report["by_platform"][r["platform"] or "(none)"] = \
            report["by_platform"].get(r["platform"] or "(none)", 0) + 1
        report["dids"].append({"did": r["did"], "name": r["display_name"],
                               "platform": r["platform"], "age_days": r["age_days"]})

    if not dry_run and rows:
        await conn.executemany(
            "UPDATE agents SET revoked_at = now(), revocation_reason = $2 "
            "WHERE did = $1 AND revoked_at IS NULL",
            [(r["did"], REASON) for r in rows],
        )
        report["revoked"] = len(rows)

    await conn.close()
    print(json.dumps(report, indent=1))

    if not dry_run and rows:
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
        if token and chat:
            import httpx
            by = ", ".join(f"{k} {v}" for k, v in sorted(report["by_platform"].items()))
            httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                       data={"chat_id": chat,
                             "text": f"MolTrust — {len(rows)} DIDs als {REASON} revoked\n\n"
                                     f"{by}\n\nNie ein authentifizierter Aufruf, "
                                     f"aelter als {days} Tage. Umkehrbar."})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=INACTIVE_DAYS)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.dry_run, a.days)))
