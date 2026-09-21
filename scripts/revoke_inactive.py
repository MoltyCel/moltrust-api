#!/usr/bin/env python3
"""Revoke DIDs that never made an authenticated call.
See docs/decisions/ADR-inactive-did-revocation.md.

Deliberately narrow: only agents that were never active, never those that went
quiet. An agent with one call on day two and silence since has arrived; that is
a retention question, not a counting one.

**Listing is the default.** Revoking needs both `--apply` and the armed flag
REVOKE_INACTIVE_ARMED, because this script is now in cron and a flag that only
lives in an argument is one edited crontab line away from firing. The arming
rule is written up in CLAUDE.md; without it `--apply` refuses and says so.

    python3 scripts/revoke_inactive.py               # list, change nothing
    python3 scripts/revoke_inactive.py --apply       # refuses unless armed
    REVOKE_INACTIVE_ARMED=1 … --apply                # the only way it revokes

Cron: Sunday 05:00 UTC, listing only. The candidate list goes to the stats
channel — it is a number nobody has to act on until someone decides to.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import datetime as _dt

import asyncpg

from app import notify

notify.silence_http_request_logs()

INACTIVE_DAYS = 90
REASON = "inactive_90d"
ARM_FLAG = "REVOKE_INACTIVE_ARMED"

# Caller identity has only been recorded since #342. Before that date
# usage_daily_keys is empty for everyone, so "no row" means "not measured", not
# "never called". A first dry run without this guard proposed 76 agents, nearly
# all of them from before the telemetry existed.
TELEMETRY_EPOCH = _dt.date(2026, 9, 14)


async def candidates(conn, days: int):
    return await conn.fetch(
        """SELECT a.did, a.display_name, a.platform, a.created_at,
                  (current_date - a.created_at::date) AS age_days,
                  a.last_seen,
                  (a.created_at + ($1 || ' days')::interval)::date AS due_on
             FROM agents a
             LEFT JOIN usage_daily_keys k ON k.did = a.did
            WHERE a.revoked_at IS NULL
              AND a.created_at < now() - ($1 || ' days')::interval
              AND a.created_at >= $2
              AND k.did IS NULL
              -- Our own service agents never authenticate against us and must
              -- not be revoked for it. This catches agent_type='system' only;
              -- see the note in CLAUDE.md about the ones that carry
              -- agent_type='external' despite being ours.
              AND coalesce(a.agent_type, 'external') <> 'system' 
            GROUP BY a.did, a.display_name, a.platform, a.created_at, a.last_seen
            ORDER BY a.created_at""",
        str(days), TELEMETRY_EPOCH,
    )


def _armed() -> bool:
    return os.getenv(ARM_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def _summary(report: dict, days: int) -> str:
    """The message the stats channel gets. Listing only — nothing to act on."""
    n = report["candidates"]
    if not n:
        return (f"\U0001f9f9 Inaktive DIDs — keine Kandidaten\n\n"
                f"Nie ein authentifizierter Aufruf, älter als {days} Tage: 0.")
    by = ", ".join(f"{k} {v}" for k, v in sorted(report["by_platform"].items()))
    lines = [f"\U0001f9f9 Inaktive DIDs — {n} Kandidaten (Vorschau, nichts geändert)",
             "",
             f"Nie ein authentifizierter Aufruf, älter als {days} Tage.",
             f"Nach Plattform: {by}",
             ""]
    for d in report["dids"][:25]:
        lines.append(f"{d['did']}  ·  {d['platform'] or '-'}  ·  "
                     f"zuletzt gesehen {d['last_seen'] or 'nie'}  ·  "
                     f"fällig seit {d['due_on']}")
    if n > 25:
        lines.append(f"… und {n - 25} weitere, vollständig im Log.")
    lines += ["", f"Scharf läuft das nur mit --apply und {ARM_FLAG}=1."]
    return "\n".join(lines)


async def main(apply_changes: bool, days: int) -> int:
    conn = await asyncpg.connect(host="localhost", user="moltstack", database="moltstack")
    rows = await candidates(conn, days)
    armed = _armed()
    will_revoke = apply_changes and armed and bool(rows)

    report = {
        "inactive_days": days,
        "telemetry_epoch": TELEMETRY_EPOCH.isoformat(),
        "note": "agents registered before the telemetry epoch are out of scope: "
                "no usage row means unmeasured, not inactive",
        "candidates": len(rows),
        "apply_requested": apply_changes,
        "armed": armed,
        "will_revoke": will_revoke,
        "by_platform": {},
        "dids": [],
    }
    for r in rows:
        key = r["platform"] or "(none)"
        report["by_platform"][key] = report["by_platform"].get(key, 0) + 1
        report["dids"].append({
            "did": r["did"], "name": r["display_name"], "platform": r["platform"],
            "age_days": r["age_days"],
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            "due_on": r["due_on"].isoformat(),
        })

    if apply_changes and not armed:
        report["refused"] = (f"--apply given but {ARM_FLAG} is not set. "
                             f"Nothing was revoked.")

    if will_revoke:
        await conn.executemany(
            "UPDATE agents SET revoked_at = now(), revocation_reason = $2 "
            "WHERE did = $1 AND revoked_at IS NULL",
            [(r["did"], REASON) for r in rows],
        )
        report["revoked"] = len(rows)

    await conn.close()
    print(json.dumps(report, indent=1))

    if will_revoke:
        by = ", ".join(f"{k} {v}" for k, v in sorted(report["by_platform"].items()))
        notify.send_telegram(
            f"MolTrust — {len(rows)} DIDs als {REASON} revoked\n\n{by}\n\n"
            f"Nie ein authentifizierter Aufruf, älter als {days} Tage. Umkehrbar.",
            channel=notify.ALERTS)
    elif apply_changes and not armed:
        notify.send_telegram(
            f"\u26a0\ufe0f revoke_inactive: --apply ohne {ARM_FLAG}. "
            f"{len(rows)} Kandidaten, nichts geändert.", channel=notify.ALERTS)
    else:
        notify.send_telegram(_summary(report, days), channel=notify.STATS)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help=f"actually revoke; additionally requires {ARM_FLAG}")
    ap.add_argument("--dry-run", action="store_true",
                    help="accepted and ignored — listing is the default")
    ap.add_argument("--days", type=int, default=INACTIVE_DAYS)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.apply, a.days)))
