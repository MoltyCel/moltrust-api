#!/usr/bin/env python3
"""Write registry-proof.json: the public count, and the anchors behind it.

The page at moltrust.ch/registry-proof.html carries no number of its own. It
fetches this file and fills every figure from it at load time, so a count that
changes in the database changes on the page and nowhere has to be edited by
hand. The counts come from app/sql/public_count.sql through app.public_count —
the same file the milestone trigger reads, so the page and the alert cannot
disagree.

What a third party can do with the result:

  * take any `credentials` row, walk `path` from `leaf`, and arrive at `root`
  * fetch the `tx` of that root from any Base endpoint, read the calldata as
    UTF-8, and find `MolTrust/VC/v1/<root>`

That proves the listed credential sat in a batch whose root was on chain at the
stated block, which is what stops us extending the list afterwards. It does not
prove the `activated` column: that comes from our request log and has no anchor.
The page says so where the column appears.

What is deliberately not in here: wallet addresses, API keys, IP addresses,
request paths, and the platform an agent registered from. The bucket says
`partner` and never which partner — Ownify and aeoess have not been asked
whether their agents may be listed by name, so they are not.

    python3 scripts/registry_proof_export.py --out /tmp/registry-proof.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import public_count  # noqa: E402 - path set before the import

CALLDATA_PREFIX = "MolTrust/VC/v1"

# Same bucketing as app/sql/public_count.sql. Kept as one SQL string rather than
# a second definition: if the two ever disagree the totals stop adding up, and
# the completeness check at the end of this file is what catches it.
ROWS_SQL = r"""
SELECT a.did,
       c.credential_type,
       c.issued_at::date::text,
       CASE
         WHEN a.agent_type <> 'external'                        THEN 'intern'
         WHEN a.platform IN ('test','system','moltrust','gate') THEN 'intern'
         WHEN a.platform IN ('ownify','aeoess','klaw')
              AND a.display_name ~* '(test|demo|sample|dummy|probe|staging)'
                                                                THEN 'partner-test'
         WHEN a.platform IN ('ownify','aeoess','klaw')          THEN 'partner'
         WHEN a.platform IN ('taskmarket','a2a')                THEN 'bounty'
         ELSE 'organisch'
       END,
       ca.merkle_root,
       ca.tx_hash,
       ca.block,
       ca.anchored_at::text,
       ca.merkle_proof::text
  FROM credential_anchors ca
  JOIN credentials c ON c.id = ca.credential_id
  JOIN agents a      ON a.did = c.subject_did
 WHERE NOT c.revoked AND a.revoked_at IS NULL
 ORDER BY ca.anchored_at, ca.credential_id
"""


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-F", "\x1f", "-t", "-P", "pager=off", "-c", sql],
        capture_output=True, text=True, timeout=180)
    if out.returncode:
        raise SystemExit(f"psql: {out.stderr[:400]}")
    return [line.split("\x1f") for line in out.stdout.strip().split("\n") if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/registry-proof.json")
    ap.add_argument("--include-internal", action="store_true",
                    help="list the deducted rows too; off by default, they are "
                         "counted in the header and not shown")
    args = ap.parse_args()

    measured = public_count.read()          # raises if completeness fails
    rows = psql(ROWS_SQL)

    creds, anchors, dropped = [], {}, 0
    for did, ctype, issued, bucket, root, tx, block, anchored_at, proof_json in rows:
        try:
            proof = json.loads(proof_json)
        except (ValueError, TypeError):
            proof = None
        leaf = (proof or {}).get("leaf")
        path = (proof or {}).get("path") or (proof or {}).get("siblings")
        if not leaf or path is None or (proof or {}).get("root") != root:
            # A row whose stored proof does not name the root it was anchored
            # against cannot be replayed, and publishing it would invite a
            # reader to conclude the anchor is broken. It is counted instead.
            dropped += 1
            continue

        a = anchors.setdefault(root, {"root": root, "tx": tx, "block": int(block) if block else None,
                                      "anchored_at": anchored_at, "credentials": 0,
                                      "calldata": f"{CALLDATA_PREFIX}/{root}"})
        a["credentials"] += 1

        if bucket in ("intern", "partner-test") and not args.include_internal:
            continue
        creds.append({"did": did, "type": ctype, "issued": issued, "bucket": bucket,
                      "leaf": leaf, "root": root, "path": path})

    body = {
        "counts": measured["counts"],
        "buckets": measured["buckets"],
        "per_day": measured["per_day"],
        # A string, not a float: Python writes 35.0 and JSON.stringify writes
        # 35, so a float in the digested body makes the browser check fail
        # against a file the terminal check accepts. Everything else here is
        # an integer or a string, and the digest stays reproducible in both.
        "rate7": f"{measured['rate7']:.2f}",
        "target": 1000,
        "log_window_starts": measured["log_window_starts"],
        "anchors": sorted(anchors.values(), key=lambda a: (a["anchored_at"] or "")),
        "credentials": creds,
    }
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chain": "eip155:8453",
        "calldata_prefix": CALLDATA_PREFIX,
        "leaf_preimage": "sha256(credential_id|subject_did|credential_type|issued_at|proof_value)",
        # Counts a reader checks before trusting anything below them. Rows that
        # could not be replayed are named rather than quietly left out.
        "integrity": {
            "credentials_listed": len(creds),
            "credentials_anchored": len(rows),
            "unreplayable_dropped": dropped,
            "roots": len(anchors),
            "sha256": hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        },
        **body,
    }

    listed = {b["bucket"]: b for b in measured["buckets"]}
    expected = sum(listed[k]["anchored"] for k in ("bounty", "partner", "organisch")
                   if k in listed)
    if not args.include_internal and len(creds) < expected:
        # More credentials than agents is normal — an agent can hold several.
        # Fewer means rows went missing, and then no file is written.
        raise SystemExit(f"only {len(creds)} listed credentials for {expected} counted "
                         f"agents; refusing to write a short file")

    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, args.out)
    print(f"{args.out}: {len(creds)} credentials, {len(anchors)} roots, "
          f"{dropped} unreplayable, public count {measured['counts']['public']}, "
          f"{os.path.getsize(args.out) // 1024} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
