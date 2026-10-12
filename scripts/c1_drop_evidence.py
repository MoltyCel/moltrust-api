#!/usr/bin/env python3
"""Remove `evidence` from stored credentials, in one checked transaction (ops C1).

Until 2026-10-12 the anchor batch wrote the anchor into `raw_vc` as `evidence`
after the issuer had signed the document. No stored credential verified as
stored; 702 of 716 verified once `evidence` was removed, 14 in no variant. The
anchor itself lives unchanged in credential_anchors (the five-column leaf never
read raw_vc), so removing the field breaks no anchor.

    python3 scripts/c1_drop_evidence.py            # dry run, ends in ROLLBACK
    python3 scripts/c1_drop_evidence.py --apply    # backup, then COMMIT if every count holds

Order of a run:
 1. Refuse if this checkout's anchor.py still writes evidence: the next batch
    (every two hours) would put it back on new rows.
 2. --apply only: pg_dump of `credentials` into a 0600 file; its sha256 is printed.
 3. Before: the 67 rows with an ipfs_cid are fetched from the gateway and compared
    with raw_vc, and the conformance commitment of app/main.py
    (sha256(json.dumps(vc, sort_keys=True))) is computed over both.
 4. One transaction: every row carrying `evidence` is locked; each one without
    it must verify against its own verificationMethod (key from the published
    DID document), except the 14 known ones. One other failure: ROLLBACK, the id
    and the reason are printed, nothing else.
 5. UPDATE ... SET raw_vc = raw_vc - 'evidence' for exactly those ids, then the
    counts in the same transaction: carrying evidence 0, failing beside the 14
    0, the 14 still unverifiable. Any deviation: ROLLBACK.
 6. COMMIT on --apply, ROLLBACK otherwise.
 7. After: leaves recomputed from the five columns against
    credential_anchors.merkle_proof.leaf; the IPFS comparison and the
    commitment again.

Prints ids, counts and hashes of public documents. No key, no credential body.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts import credentials_verify as cv  # noqa: E402

# Public gateways throttle hard (429 after a burst, measured 12.10.2026); the
# comparison goes slowly and tries each in turn. A document no gateway hands
# out is counted as unreadable, never as equal or different.
GATEWAYS = ("https://gateway.pinata.cloud/ipfs/", "https://ipfs.io/ipfs/")
PAUSE_S = 4
# Pinata answers 403 to urllib's default "Python-urllib/3.x" (measured 12.10.2026
# by the session that drafted #745-#748). Without a header of its own every
# document came back unreadable and the run sat in retry pauses.
USER_AGENT = "moltrust-migration/2026-10-12"

# The one MolTrustConformityDeclaration (credential 1025). Its commitment
# (app/main.py, sha256(json.dumps(vc, sort_keys=True))) went out once in the
# issuing response and is stored nowhere; this is the value over the signed
# form, without evidence (handoff on #761, 12.10.2026).
CONFORMITY_ID = 1025
CONFORMITY_COMMITMENT = "330b02f3a77d348248572f8fc70522ca71519c5f2ad5402f9791fd6806946711"
BACKUP_DIR = pathlib.Path.home() / "backups"


def commitment(doc: dict) -> str:
    """app/main.py, conformance commitment over an issued document."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def anchor_still_writes_evidence() -> bool:
    src = (REPO / "app" / "provenance" / "anchor.py").read_text(encoding="utf-8")
    return "jsonb_set(raw_vc" in src or "'{evidence}'" in src


def backup() -> tuple[pathlib.Path, str]:
    BACKUP_DIR.mkdir(mode=0o700, exist_ok=True)
    path = BACKUP_DIR / f"c1-credentials-{dt.datetime.utcnow():%Y%m%dT%H%M%SZ}.sql"
    env = dict(os.environ, PGPASSWORD=os.environ.get("MOLTSTACK_DB_PW", ""))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        subprocess.run(["pg_dump", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
                        "-t", "credentials", "--no-owner"], stdout=out, env=env, check=True)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_ipfs(cid: str, cache: dict) -> dict | None:
    if cid in cache:
        return cache[cid]
    doc = None
    for attempt in range(3):
        for gw in GATEWAYS:
            try:
                time.sleep(PAUSE_S)
                req = urllib.request.Request(gw + cid, headers={"User-Agent": USER_AGENT,
                                                               "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 - fixed https gateways
                    doc = json.load(r)
                break
            except Exception:
                continue
        if doc is not None:
            break
        time.sleep(30 * (attempt + 1))
    cache[cid] = doc
    return doc


def ipfs_compare(rows: list[tuple[int, dict, str]], cache: dict) -> dict:
    """rows: (id, raw_vc, cid). Equal commitments (app/main.py), and unreadable."""
    equal = unreadable = 0
    for _cid_row, raw, cid in rows:
        doc = fetch_ipfs(cid, cache)
        if doc is None:
            unreadable += 1
            continue
        if commitment(doc) == commitment(raw):
            equal += 1
    return {"rows": len(rows), "equal": equal, "unreadable": unreadable}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    import psycopg2
    import psycopg2.extras
    from app.provenance.anchor import _credential_leaves

    if anchor_still_writes_evidence():
        print("REFUSED: app/provenance/anchor.py in this checkout still writes evidence")
        return 1

    keys = cv.keys_from_did_document(cv._fetch_did_document(cv.DID_DOCUMENT_URL))
    if not keys:
        print("REFUSED: no Ed25519 key in the published DID document")
        return 1

    report: dict = {"mode": "apply" if args.apply else "dry-run"}
    if args.apply:
        path, digest = backup()
        report["backup"] = {"file": str(path), "sha256": digest}

    conn = psycopg2.connect(host="localhost", dbname="moltstack", user="moltstack",
                            password=os.environ.get("MOLTSTACK_DB_PW"))
    conn.autocommit = False
    cur = conn.cursor()

    def ipfs_rows():
        cur.execute("SELECT id, raw_vc, ipfs_cid FROM credentials "
                    "WHERE ipfs_cid IS NOT NULL AND raw_vc IS NOT NULL ORDER BY id")
        return [(i, v if isinstance(v, dict) else json.loads(v), c) for i, v, c in cur.fetchall()]

    ipfs_cache: dict = {}
    report["ipfs_before"] = ipfs_compare(ipfs_rows(), ipfs_cache)

    def conformity():
        cur.execute("SELECT raw_vc FROM credentials WHERE id = %s", (CONFORMITY_ID,))
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        doc = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return commitment(doc) == CONFORMITY_COMMITMENT

    report["conformity_commitment_before"] = conformity()

    cur.execute("SELECT id, raw_vc FROM credentials WHERE raw_vc ? 'evidence' ORDER BY id FOR UPDATE")
    locked = [(i, v if isinstance(v, dict) else json.loads(v)) for i, v in cur.fetchall()]
    report["carrying_evidence_before"] = len(locked)

    ids = []
    for cid, vc in locked:
        stripped = {k: v for k, v in vc.items() if k != "evidence"}
        if not cv.verifies(stripped, keys) and cid not in cv.KNOWN_UNVERIFIABLE:
            conn.rollback()
            print(f"ROLLBACK: id {cid} does not verify without evidence")
            return 1
        ids.append(cid)

    cur.execute("UPDATE credentials SET raw_vc = raw_vc - 'evidence' WHERE id = ANY(%s)", (ids,))
    report["updated"] = cur.rowcount

    cur.execute("SELECT count(*) FROM credentials WHERE raw_vc ? 'evidence'")
    report["carrying_evidence_after"] = cur.fetchone()[0]
    cur.execute("SELECT id, raw_vc FROM credentials WHERE raw_vc IS NOT NULL ORDER BY id")
    rows = [(i, v if isinstance(v, dict) else json.loads(v)) for i, v in cur.fetchall()]
    failing = [cid for cid, vc in rows if not cv.verifies(vc, keys)]
    beside = [c for c in failing if c not in cv.KNOWN_UNVERIFIABLE]
    report["total"] = len(rows)
    report["verifying"] = len(rows) - len(failing)
    report["failing_known"] = len(failing) - len(beside)
    report["failing_beside_known"] = beside

    ok = (report["carrying_evidence_after"] == 0 and not beside
          and report["failing_known"] == len(cv.KNOWN_UNVERIFIABLE)
          and report["updated"] == len(ids))
    if not ok:
        conn.rollback()
        report["result"] = "ROLLBACK: a count deviates"
        print(json.dumps(report, indent=2, default=str))
        return 1

    if args.apply:
        conn.commit()
        report["result"] = "COMMIT"
    else:
        conn.rollback()
        report["result"] = "ROLLBACK (dry run)"

    cur.execute("""SELECT c.id, c.subject_did, c.credential_type, c.issued_at, c.proof_value,
                          a.merkle_proof
                     FROM credentials c JOIN credential_anchors a ON a.credential_id = c.id
                    ORDER BY c.id""")
    anchored = cur.fetchall()
    recs = [{"id": r[0], "subject_did": r[1], "credential_type": r[2], "issued_at": r[3],
             "proof_value": r[4]} for r in anchored]
    leaves = _credential_leaves(recs)
    stored = [(r[5] if isinstance(r[5], dict) else json.loads(r[5])).get("leaf") for r in anchored]
    report["leaves"] = {"anchored": len(recs),
                        "equal": sum(1 for a, b in zip(leaves, stored) if a == b)}
    report["ipfs_after"] = ipfs_compare(ipfs_rows(), ipfs_cache)
    report["conformity_commitment_after"] = conformity()
    conn.close()
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
