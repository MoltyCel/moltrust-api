#!/usr/bin/env python3
"""Suspend or reactivate a reseller. Ops tool (ops job RES-SUSP).

Usage:
  set -a; source ~/.moltrust_secrets; set +a   # DB creds
  python3 scripts/suspend_reseller.py --login ownify --reason "..."
  python3 scripts/suspend_reseller.py --login ownify --reason "..." --reactivate

Suspension is a state change, never a deletion: reseller_accounts.status ->
'suspended' (+ suspended_at, suspend_reason), open reseller_sessions are set
revoked=true, and one reseller_admin_audit row is written (action 'suspend' /
'reactivate', detail {"reason": ..., "revoked_sessions": n}). agent_payer,
agents, audit rows and master data are not touched.

The script performs NO schema changes: it refuses to run until migration
app/migrations/2026-10-16_reseller_suspend.sql has been applied. It prints
before/after state (no password hashes, no secrets).
"""
import argparse
import asyncio
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.reseller import (  # noqa: E402
    ResellerNotFound, ResellerStateConflict, set_reseller_status,
)

_STATE_SQL = (
    "SELECT payer_ref, login, display_name, active, status, suspended_at, suspend_reason, "
    "(SELECT count(*) FROM agent_payer ap WHERE ap.payer_ref = r.payer_ref) AS agent_payer_rows, "
    "(SELECT count(*) FROM reseller_sessions s WHERE s.payer_ref = r.payer_ref "
    "   AND NOT s.revoked AND s.expires_at > now()) AS open_sessions "
    "FROM reseller_accounts r WHERE login = $1"
)


def _show(label, row):
    print(f"{label}: payer_ref={row['payer_ref']} login={row['login']} "
          f"display_name={row['display_name']} active={row['active']} status={row['status']} "
          f"suspended_at={row['suspended_at']} suspend_reason={row['suspend_reason']!r} "
          f"agent_payer_rows={row['agent_payer_rows']} open_sessions={row['open_sessions']}")


async def _run(args):
    conn = await asyncpg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME", "moltstack"),
        user="moltstack",
        password=os.getenv("MOLTSTACK_DB_PW"),
    )
    try:
        has_col = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'reseller_accounts' AND column_name = 'status'"
        )
        if not has_col:
            print("error: reseller_accounts.status missing — apply "
                  "app/migrations/2026-10-16_reseller_suspend.sql first", file=sys.stderr)
            return 3
        login = args.login.strip().lower()
        before = await conn.fetchrow(_STATE_SQL, login)
        if not before:
            print(f"error: no reseller with login {login!r}", file=sys.stderr)
            return 4
        _show("before", before)
        new_status = "active" if args.reactivate else "suspended"
        try:
            res = await set_reseller_status(conn, before["payer_ref"], new_status, args.reason, args.actor)
        except ResellerNotFound:
            print("error: reseller not found", file=sys.stderr)
            return 4
        except ResellerStateConflict as e:
            print(f"error: {e}", file=sys.stderr)
            return 5
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"{res['action']}: ok, revoked_sessions={res['revoked_sessions']}")
        _show("after", await conn.fetchrow(_STATE_SQL, login))
        audit = await conn.fetchrow(
            "SELECT id, ts, actor, action, detail, row_hash FROM reseller_admin_audit "
            "WHERE target_payer_ref = $1 ORDER BY id DESC LIMIT 1", before["payer_ref"],
        )
        print(f"audit: id={audit['id']} ts={audit['ts']} actor={audit['actor']} "
              f"action={audit['action']} detail={audit['detail']} row_hash={audit['row_hash']}")
        return 0
    finally:
        await conn.close()


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--login", required=True)
    p.add_argument("--reason", required=True, help="mandatory, written to reseller_admin_audit")
    p.add_argument("--reactivate", action="store_true", help="reactivate instead of suspend")
    p.add_argument("--actor", default="lars-cli", help="audit actor (default: lars-cli)")
    args = p.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
