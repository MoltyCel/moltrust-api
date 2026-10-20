#!/usr/bin/env python3
"""Copy credentials published to IPFS before ops IP-1 into vc_archive.

Until IP-1 POST /credentials/issue pinned the full credential to IPFS and kept
the CID in credentials.ipfs_cid. Those CIDs stay as they are; this script reads
each document back from a public gateway and writes it to vc_archive with
origin 'ipfs-backfill' and the old CID. It never unpins and never deletes.

    python3 scripts/vc_archive_backfill.py            # dry run, writes nothing
    python3 scripts/vc_archive_backfill.py --apply    # one transaction, then counts

A dry run reads the database (read-only transaction) and the gateways and
prints counts only: candidates, readable documents, documents equal to the
stored raw_vc, documents already archived, and rows an --apply would write.
A document no gateway hands out is counted as unreadable and skipped, never
guessed. A pinned hash document (IP-1 and later) is not a credential and is
skipped as well.

Prints counts and CIDs of public documents. No credential body, no key.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app import vc_archive  # noqa: E402

# Same gateways, pause and User-Agent as scripts/c1_drop_evidence.py, measured
# there on 12.10.2026: public gateways throttle after a burst, and Pinata
# answers 403 to urllib's default User-Agent.
GATEWAYS = ("https://gateway.pinata.cloud/ipfs/", "https://ipfs.io/ipfs/")
PAUSE_S = 4
USER_AGENT = "moltrust-migration/2026-10-20"
SOURCE = "credentials.ipfs_cid"


def fetch(cid: str, pause: float = PAUSE_S) -> dict | None:
    for attempt in range(3):
        for gw in GATEWAYS:
            try:
                time.sleep(pause)
                req = urllib.request.Request(gw + cid, headers={
                    "User-Agent": USER_AGENT, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 - fixed https gateways
                    doc = json.load(r)
                return doc if isinstance(doc, dict) else None
            except Exception:  # noqa: BLE001 - the next gateway, then a pause
                continue
        time.sleep(30 * (attempt + 1) if pause else 0)
    return None


def is_credential(doc: dict) -> bool:
    """A full credential, not a pinned hash document."""
    return isinstance(doc.get("credentialSubject"), dict) and "vc_hash" not in doc


def plan(rows, archived_hashes: set, fetcher=fetch) -> tuple[dict, list]:
    """rows: (credential id, raw_vc, cid). Counts, and the rows to write."""
    counts = {"candidates": len(rows), "readable": 0, "unreadable": 0,
              "not_a_credential": 0, "equal_to_raw_vc": 0, "already_archived": 0,
              "to_write": 0}
    writes = []
    for _cred_id, raw, cid in rows:
        doc = fetcher(cid)
        if doc is None:
            counts["unreadable"] += 1
            continue
        counts["readable"] += 1
        if not is_credential(doc):
            counts["not_a_credential"] += 1
            continue
        h = vc_archive.vc_hash(doc)
        # Compared in JCS form: json.dumps keeps 0.0 and 0 apart, and jsonb
        # hands numbers back in a form the gateway's JSON does not always share.
        if raw is not None and h == vc_archive.vc_hash(raw):
            counts["equal_to_raw_vc"] += 1
        if h in archived_hashes:
            counts["already_archived"] += 1
            continue
        archived_hashes.add(h)
        writes.append(vc_archive.row(doc, SOURCE, ipfs_cid=cid, origin="ipfs-backfill"))
        counts["to_write"] += 1
    return counts, writes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    import psycopg2

    conn = psycopg2.connect(host="localhost", dbname="moltstack", user="moltstack",
                            password=os.environ.get("MOLTSTACK_DB_PW", ""))
    conn.set_session(readonly=not args.apply)
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.vc_archive') IS NOT NULL")
    table = cur.fetchone()[0]
    if args.apply and not table:
        print("vc_archive does not exist: apply migrations/2026-10-20_vc_archive.sql first")
        return 2
    archived: set = set()
    if table:
        cur.execute("SELECT vc_hash FROM vc_archive")
        archived = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT id, raw_vc, ipfs_cid FROM credentials "
                "WHERE ipfs_cid IS NOT NULL ORDER BY id")
    rows = cur.fetchall()

    counts, writes = plan(rows, archived)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run",
                      "vc_archive_exists": bool(table), **counts}))
    if not args.apply:
        conn.rollback()
        return 0
    # INSERT_SQL is written for asyncpg ($n); psycopg2 takes %s.
    sql = vc_archive.INSERT_SQL
    for i in range(9, 0, -1):
        sql = sql.replace(f"${i}", "%s")
    written = 0
    for values in writes:
        cur.execute(sql, values)
        written += cur.rowcount
    if written != len(writes):
        conn.rollback()
        print(f"ROLLBACK: wrote {written}, expected {len(writes)}")
        return 1
    conn.commit()
    print(json.dumps({"written": written}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
