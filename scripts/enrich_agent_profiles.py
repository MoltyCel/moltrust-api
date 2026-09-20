#!/usr/bin/env python3
"""Recompute the observed half of every agent profile.

    python3 scripts/enrich_agent_profiles.py            # stale + missing only
    python3 scripts/enrich_agent_profiles.py --all      # every agent
    python3 scripts/enrich_agent_profiles.py --dry-run  # count, write nothing

Runs daily. The observation is derived from request_log, which keeps 30 days, so
a profile is a rolling window and not a history — an agent that was busy in July
and silent since looks silent here, correctly.

What this job cannot see is worth stating, because the empty columns otherwise
read like failures:

  taskmarket_tasks  submissions live in the contract's history and the CLI
                    exposes no way to read their bodies, so a submission cannot
                    be matched to a DID without an indexer.
  erc8004_skills    needs a tokenURI fetch per agent (--skills).
  a2a_skills        needs the agent's own card fetched from agent_card_url
                    (--skills).

`--skills` makes outbound HTTP to addresses the agents themselves supplied, so it
is opt-in and off by default rather than something the daily run does quietly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import asyncpg  # noqa: E402

from app.agent_profile import enrich_agent  # noqa: E402

STALE_AFTER = "24 hours"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every agent, not just stale ones")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    args = ap.parse_args()

    # Same default as app/main.py. The service reads its DSN from the
    # environment and falls back to this; a cron entry that had to export it
    # separately would drift from the app the first time either moved.
    dsn = os.environ.get("DATABASE_URL", "postgresql://moltstack@localhost/moltstack")

    conn = await asyncpg.connect(dsn)
    try:
        if args.all:
            dids = [r["did"] for r in await conn.fetch("SELECT did FROM agents ORDER BY created_at")]
        else:
            dids = [r["did"] for r in await conn.fetch(
                f"""SELECT a.did FROM agents a
                    LEFT JOIN agent_profile p ON p.did = a.did
                    WHERE p.did IS NULL OR p.enriched_at IS NULL
                       OR p.enriched_at < now() - interval '{STALE_AFTER}'
                    ORDER BY a.created_at""")]  # nosec B608 - STALE_AFTER is a module constant, never user input

        if args.dry_run:
            out = {"would_enrich": len(dids)}
            print(json.dumps(out) if args.json else f"{len(dids)} Profile waeren zu berechnen")
            return 0

        by_source = {"did": 0, "registration_ip": 0, "none": 0}
        failed = 0
        for did in dids:
            try:
                result = await enrich_agent(conn, did)
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
                failed += 1
                print(f"FEHLER {did}: {exc}", file=sys.stderr)
                continue
            if result:
                by_source[result["observed_from"]] = by_source.get(result["observed_from"], 0) + 1

        summary = {"enriched": sum(by_source.values()), "by_source": by_source, "failed": failed}
        if args.json:
            print(json.dumps(summary))
        else:
            print(f"angereichert   {summary['enriched']}")
            print(f"  per DID      {by_source['did']}")
            print(f"  per Reg-IP   {by_source['registration_ip']}")
            print(f"  ohne Daten   {by_source['none']}")
            if failed:
                print(f"fehlgeschlagen {failed}")
        # A run where nothing could be observed at all is a signal, not a success.
        return 1 if summary["enriched"] and by_source["did"] == 0 and by_source["registration_ip"] == 0 else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
