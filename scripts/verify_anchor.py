#!/usr/bin/env python3
"""Verify a MolTrust credential against its Base L2 anchor.

Standalone on purpose: it imports nothing from this repository, talks to a
public RPC endpoint, and needs no credentials of any kind. The encoding it
implements is specified in docs/spec-fakten/anchor-commitment.md — anyone
holding a credential document can run this, or rewrite it in an afternoon from
that document.

    python3 scripts/verify_anchor.py credential.json
    curl -s https://api.moltrust.ch/... | python3 scripts/verify_anchor.py -

Exit 0 if the credential's leaf replays to the root written on chain, 1 if it
does not, 2 if the check could not be completed.
"""
import argparse
import hashlib
import json
import sys
import urllib.request

DEFAULT_RPC = "https://mainnet.base.org"
PREFIX = "MolTrust/VC/v1/"


def sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def leaf_preimage(doc: dict) -> str:
    """The five pipe-joined fields the leaf is taken over."""
    evidence = doc["evidence"][0]
    if "credentialId" not in evidence:
        raise KeyError(
            "evidence[0].credentialId missing — this document predates "
            "2026-09-21. Re-fetch it, or supply the id with --credential-id."
        )
    types = [t for t in doc["type"] if t != "VerifiableCredential"]
    if len(types) != 1:
        raise ValueError(f"cannot pick a credential type from {doc['type']}")

    # proof.created rather than validFrom/issuanceDate: the two W3C data model
    # versions name the top-level field differently, this one is in both.
    created = doc["proof"]["created"]
    if created.endswith("Z"):
        issued_at = created[:-1]
    elif created.endswith("+00:00"):
        issued_at = created[:-6]
    else:
        raise ValueError(f"proof.created is not UTC: {created}")

    return "|".join([
        str(evidence["credentialId"]),
        doc["credentialSubject"]["id"],
        types[0],
        issued_at,
        doc["proof"]["proofValue"],
    ])


def replay(leaf: bytes, path: list) -> bytes:
    """Walk the sibling path from the leaf to the root."""
    node = leaf
    for step in path:
        sibling = bytes.fromhex(step["hash"])
        if step["position"] == "left":
            node = sha(sibling + node)
        elif step["position"] == "right":
            node = sha(node + sibling)
        else:
            raise ValueError(f"unknown position {step['position']!r}")
    return node


def anchor_calldata(tx_hash: str, rpc: str) -> str:
    request = urllib.request.Request(
        rpc,
        data=json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_getTransactionByHash", "params": [tx_hash],
        }).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "moltrust-verify-anchor/1.0"},
    )
    if not rpc.startswith("https://"):
        raise ValueError("RPC endpoint must be https")
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - scheme checked above
        tx = json.loads(response.read())["result"]
    if tx is None:
        raise LookupError(f"{tx_hash} not found on this chain")
    if tx["value"] != "0x0" or tx["to"].lower() != tx["from"].lower():
        raise ValueError("not a MolTrust anchor: anchors are zero-value self-sends")
    return bytes.fromhex(tx["input"][2:]).decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", help="credential JSON file, or - for stdin")
    parser.add_argument("--rpc", default=DEFAULT_RPC, help=f"default {DEFAULT_RPC}")
    parser.add_argument("--credential-id", type=int,
                        help="supply the id for a document issued before 2026-09-21")
    args = parser.parse_args()

    raw = sys.stdin.read() if args.document == "-" else open(args.document).read()
    doc = json.loads(raw)

    try:
        evidence = doc["evidence"][0]
    except (KeyError, IndexError):
        print("no evidence block — this credential is not anchored", file=sys.stderr)
        return 2

    if args.credential_id is not None:
        evidence["credentialId"] = args.credential_id

    try:
        preimage = leaf_preimage(doc)
        leaf = sha(preimage.encode())
        root = replay(leaf, evidence["merkleProof"])
        calldata = anchor_calldata(evidence["txHash"], args.rpc)
    except Exception as exc:  # noqa: BLE001 - the message is the output
        print(f"could not verify: {exc}", file=sys.stderr)
        return 2

    print(f"preimage  {preimage[:72]}{'…' if len(preimage) > 72 else ''}")
    print(f"leaf      {leaf.hex()}")
    print(f"root      {root.hex()}")
    print(f"calldata  {calldata}")

    if not calldata.startswith(PREFIX):
        print(f"\nFAIL: calldata does not carry the {PREFIX} prefix", file=sys.stderr)
        return 1
    if leaf.hex() != evidence.get("leaf", leaf.hex()):
        print(f"\nFAIL: recomputed leaf differs from the one in the document "
              f"({evidence['leaf']})", file=sys.stderr)
        return 1
    if root.hex() != calldata[len(PREFIX):]:
        print(f"\nFAIL: replayed root is not the anchored one", file=sys.stderr)
        return 1

    print(f"\nOK: anchored in {evidence['txHash']}"
          + (f", block {evidence['blockNumber']}" if evidence.get("blockNumber") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
