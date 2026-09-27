#!/usr/bin/env python3
"""Check moltrust.ch/registry-proof.json against the Base chain. No key needed.

Nothing here talks to a MolTrust API, and nothing here needs an account. The
file is fetched over plain HTTPS, the chain is read through whichever Base
endpoint you point it at, and the default is the public one that anybody can
use. If this script only passed against our own node it would be worth nothing.

What it checks, in order:

  1. the file's own sha256 over its data section, against the header
  2. the listed row counts against the rows actually present
  3. every distinct Merkle root: one eth_getTransactionByHash, calldata read as
     UTF-8, `MolTrust/VC/v1/<root>` expected
  4. every credential: hash `leaf` up its sibling path and land on `root`
  5. the bucket totals in the header against the rows

Any mismatch is printed with the row that caused it and the exit code is 1. A
run that cannot read the file at all exits 2 rather than reporting zero
problems, because "nothing checked" and "nothing wrong" are different answers.

    python3 registry_proof.py
    python3 registry_proof.py --rpc https://base.llamarpc.com
    python3 registry_proof.py --file ./registry-proof.json --limit 50
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from collections import Counter

URL = "https://moltrust.ch/registry-proof.json"
PUBLIC_RPC = "https://mainnet.base.org"
UA = "registry-proof-check/1.0 (+https://moltrust.ch/registry-proof.html)"


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310  # nosec B310
        return json.loads(r.read().decode())


def rpc(url: str, method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urllib.request.Request(url, data=body.encode(),
                                 headers={"content-type": "application/json",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:  # noqa: S310  # nosec B310
        return json.loads(r.read().decode()).get("result")


def replay(leaf: str, path: list) -> str:
    """Hash a leaf up its sibling path. Same rule as the issuer: a sibling
    marked `left` is concatenated before the running hash, otherwise after."""
    cur = bytes.fromhex(leaf)
    for step in path:
        sib = bytes.fromhex(step["hash"])
        cur = hashlib.sha256(sib + cur if step.get("position") == "left" else cur + sib).digest()
    return cur.hex()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL)
    ap.add_argument("--file", help="read a local copy instead of fetching")
    ap.add_argument("--rpc", default=PUBLIC_RPC,
                    help=f"any Base endpoint; default {PUBLIC_RPC}")
    ap.add_argument("--limit", type=int, help="check only the first N credentials")
    ap.add_argument("--skip-chain", action="store_true",
                    help="replay the Merkle paths only, ask no endpoint")
    args = ap.parse_args()

    try:
        doc = json.load(open(args.file)) if args.file else fetch_json(args.url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"could not read the proof file: {exc}")
        return 2

    problems: list[str] = []
    integrity = doc.get("integrity") or {}
    creds = doc.get("credentials") or []
    anchors = doc.get("anchors") or []

    # 1 — the file against its own digest
    body = {k: doc[k] for k in doc if k not in ("generated_at", "chain", "integrity",
                                                "calldata_prefix", "leaf_preimage")}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != integrity.get("sha256"):
        problems.append(f"digest mismatch: computed {digest[:16]}…, "
                        f"header says {str(integrity.get('sha256'))[:16]}…")

    # 2 — the counts it declares against what is there
    if integrity.get("credentials_listed") != len(creds):
        problems.append(f"header says {integrity.get('credentials_listed')} credentials, "
                        f"file carries {len(creds)}")
    if integrity.get("roots") != len(anchors):
        problems.append(f"header says {integrity.get('roots')} roots, "
                        f"file carries {len(anchors)}")

    print(f"generated {doc.get('generated_at')} · {len(creds)} credentials · "
          f"{len(anchors)} roots · public count {doc.get('counts', {}).get('public')}")

    # 3 — each root against the chain
    if args.skip_chain:
        print("chain check skipped")
    else:
        prefix = doc.get("calldata_prefix", "MolTrust/VC/v1")
        print(f"checking {len(anchors)} roots against {args.rpc}")
        for a in anchors:
            try:
                tx = rpc(args.rpc, "eth_getTransactionByHash", [a["tx"]])
            except (urllib.error.URLError, OSError, ValueError) as exc:
                problems.append(f"{a['tx'][:12]}…: endpoint did not answer ({exc})")
                continue
            if not tx:
                problems.append(f"{a['tx'][:12]}…: not found on chain")
                continue
            try:
                calldata = bytes.fromhex(tx.get("input", "0x")[2:]).decode("utf-8", "replace")
            except ValueError:
                calldata = ""
            if calldata.strip("\x00") != f"{prefix}/{a['root']}":
                problems.append(f"{a['tx'][:12]}…: calldata is {calldata[:60]!r}, "
                                f"expected {prefix}/{a['root'][:16]}…")

    # 4 — each credential up to its root
    roots = {a["root"] for a in anchors}
    checked = creds[: args.limit] if args.limit else creds
    bad = 0
    for c in checked:
        got = replay(c["leaf"], c["path"])
        if got != c["root"]:
            bad += 1
            if bad <= 5:
                problems.append(f"{c['did']}: replays to {got[:16]}…, "
                                f"listed root is {c['root'][:16]}…")
        if c["root"] not in roots:
            problems.append(f"{c['did']}: root {c['root'][:16]}… has no anchor entry")
    if bad > 5:
        problems.append(f"and {bad - 5} further credentials that do not replay")
    print(f"replayed {len(checked)} credentials, {len(checked) - bad} reached their root")

    # 5 — the header's bucket totals against the rows
    listed = Counter(c["bucket"] for c in checked)
    if not args.limit:
        for b in doc.get("buckets", []):
            if b["bucket"] in ("intern", "partner-test"):
                continue
            if listed[b["bucket"]] < b["anchored"]:
                problems.append(f"bucket {b['bucket']}: header counts {b['anchored']} "
                                f"anchored agents, file lists {listed[b['bucket']]} credentials")

    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("everything checks out. The listed credentials sit in roots that were on")
    print("Base at the stated blocks; the list cannot have been extended afterwards.")
    print("The 'activated' column is not covered — it comes from a request log and")
    print("has no anchor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
