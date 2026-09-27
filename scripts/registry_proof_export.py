#!/usr/bin/env python3
"""Write registry-proof.json from the one export path.

Everything of substance lives in app/registry_export: the allow-list of
platforms, the partner aggregation, the snapshot that pins registrations,
anchors and the activation window together. This file is the command line
around it, and it refuses to write anything the reader cannot check.

    python3 scripts/registry_proof_export.py --out ~/blog-deploy-stage/registry-proof.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import registry_export  # noqa: E402 - path set before the import


def main() -> int:
    ap = argparse.ArgumentParser()
    # No default: the output path is a deploy decision, and a default under
    # /tmp invites a run whose file nobody meant to keep.
    ap.add_argument("--out", required=True,
                    help="where to write the JSON, e.g. the deploy staging dir")
    ap.add_argument("--as-of", help="snapshot instant; default is now, to the minute")
    args = ap.parse_args()

    doc = registry_export.build(args.as_of)     # raises if the buckets do not add up

    # Last gate before a public file. A partner DID in the output is the failure
    # this whole path exists to prevent, so it is checked here too rather than
    # trusted to hold upstream.
    for row in doc["rows"]:
        if row["bucket"] in registry_export.AGGREGATED:
            if row.get("kind") != "aggregate" or "did" in row:
                raise SystemExit(f"a {row['bucket']} row carries a DID; nothing written")
            if any("credential_id" in a for a in row.get("anchors") or []):
                raise SystemExit("aggregate anchors still carry credential_id; "
                                 "nothing written")
    if not doc["totals"]["registered_with_anchor"]:
        raise SystemExit("no counted agents; refusing to write an empty file")

    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, args.out)

    t = doc["totals"]
    print(f"{args.out}: {t['rows']} rows, {t['registered_with_anchor']} counted DIDs, "
          f"{t['anchors']} anchors, {t['roots']} roots, "
          f"activated {t['activated_counted']}, as of {doc['as_of']}, "
          f"{os.path.getsize(args.out) // 1024} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
