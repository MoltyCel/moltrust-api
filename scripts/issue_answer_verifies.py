#!/usr/bin/env python3
"""Does the answer of POST /credentials/issue verify as it is handed out.

Ops VC-1, 2026-10-20. The issue answer carries `renew_at` and, when the IPFS
publish succeeds, `ipfs_cid`/`ipfs_url` next to the signed credential. None of
them is covered by the proof, and until VC-1 POST /credentials/verify answered
valid=false for every answer sent back unchanged.

What this checks on the running code, without issuing anything: the newest
stored credential, completed with the three fields the way the issue answer
completes it, goes through app.credentials.verify_credential twice (with all
three, with `renew_at` alone) and must pass both times; the same answer with a
changed signed field must fail.

Output: one line. "geschlossen 3 von 3" when all three hold, "OFFEN <cases>"
otherwise, UNREADABLE when the stored credential cannot be read. Read-only: one
SELECT. The verify path loads the issuer key exactly as the API process does.
"""
from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Values of the shape the issue answer carries; not checked by anything.
IPFS_CID = "bafkreiexampleexampleexampleexampleexampleexampleexample"
IPFS_URL = "https://gateway.pinata.cloud/ipfs/" + IPFS_CID
RENEW_AT = "2027-01-01T00:00:00Z"


def answers(vc: dict) -> dict:
    """The issue answer as handed out, with and without the IPFS fields."""
    return {
        "ipfs": {**vc, "ipfs_cid": IPFS_CID, "ipfs_url": IPFS_URL, "renew_at": RENEW_AT},
        "no-ipfs": {**vc, "renew_at": RENEW_AT},
    }


def check(vc: dict, verify) -> list[str]:
    """Names of the cases that do not hold; empty when all three do."""
    bad = []
    for name, answer in answers(vc).items():
        if not verify(answer).get("valid"):
            bad.append(name)
    changed = copy.deepcopy(answers(vc)["ipfs"])
    subject = changed.get("credentialSubject")
    if isinstance(subject, dict):
        changed["credentialSubject"] = {**subject, "verified": not subject.get("verified")}
    else:
        changed["issuer"] = str(changed.get("issuer")) + "x"
    if verify(changed).get("valid"):
        bad.append("changed-field-passes")
    return bad


def _newest_stored() -> dict:
    import psycopg2

    conn = psycopg2.connect(host="localhost", dbname="moltstack", user="moltstack",
                            password=os.environ.get("MOLTSTACK_DB_PW"))
    try:
        cur = conn.cursor()
        cur.execute("SELECT raw_vc FROM credentials WHERE raw_vc IS NOT NULL ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise LookupError("no stored credential")
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def main() -> int:
    try:
        vc = _newest_stored()
    except Exception as exc:
        print(f"UNREADABLE database: {type(exc).__name__}")
        return 0
    from app.credentials import verify_credential

    if not verify_credential(vc).get("valid"):
        print("UNREADABLE newest stored credential does not verify as stored")
        return 0
    bad = check(vc, verify_credential)
    print("OFFEN " + ",".join(bad) if bad else "geschlossen 3 von 3")
    return 0


if __name__ == "__main__":
    sys.exit(main())
