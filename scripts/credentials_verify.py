#!/usr/bin/env python3
"""Do the stored credentials verify against their own verificationMethod.

Ops backlog C1, 2026-10-12. Of 716 stored credentials none verified as stored:
the anchor batch wrote `evidence` into `raw_vc` after the issuer had signed it
(app/provenance/anchor.py until this date), and the signature covers the
document without it. 702 verified once `evidence` was removed, 14 in no tested
variant. A holder who received a stored copy held a document that fails a
verifier's check.

What this checks, the way an outside verifier would: each row's proof names a
verificationMethod; its key comes from the published issuer DID document
(`/.well-known/did.json`), never from the signing key; app/crypto/hybrid.py
verify_proof decides. The DID document is fetched, not built from the code,
because the published one is what a verifier resolves.

Output: the number of stored credentials that do not verify, minus the 14 known
ones (KNOWN_UNVERIFIABLE, and any id in credential_verification_status once
that table exists). `--list` prints their ids. A source that cannot be read
prints UNREADABLE, which the selftest runner treats as FAIL.

Read-only: one SELECT, one GET of a public document. No private key, no KMS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DID_DOCUMENT_URL = "https://api.moltrust.ch/.well-known/did.json"

# The 14 that verify in no tested variant (credential-pruefung.md, 12.10.2026):
# 925-935 issued before the first commit, 936, 937, 940 under the canonical
# form of their day. Not revoked; marked, not deleted (C1 PR 4).
KNOWN_UNVERIFIABLE = frozenset({925, 926, 927, 928, 929, 930, 931, 932, 933, 934, 935,
                                936, 937, 940})

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ED25519_PUB_MULTICODEC = b"\xed\x01"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def ed25519_key_from_multibase(mb: str) -> bytes:
    """`z` + base58btc(0xed01 || 32-byte key) -> the 32 bytes."""
    if not mb.startswith("z"):
        raise ValueError("not base58btc multibase")
    raw = b58decode(mb[1:])
    if not raw.startswith(_ED25519_PUB_MULTICODEC) or len(raw) != 34:
        raise ValueError("not an Ed25519 public key multicodec")
    return raw[2:]


def keys_from_did_document(doc: dict) -> dict:
    """verificationMethod id -> 32-byte Ed25519 public key."""
    out = {}
    for vm in doc.get("verificationMethod") or []:
        if vm.get("type") in ("Ed25519VerificationKey2020", "Multikey") and vm.get("publicKeyMultibase"):
            try:
                out[vm["id"]] = ed25519_key_from_multibase(vm["publicKeyMultibase"])
            except ValueError:
                continue
    return out


def first_proof(vc: dict) -> dict | None:
    p = vc.get("proof")
    if isinstance(p, list):
        p = next((x for x in p if isinstance(x, dict)
                  and "Ed25519" in str(x.get("type", "")) + str(x.get("cryptosuite", ""))),
                 p[0] if p and isinstance(p[0], dict) else None)
    return p if isinstance(p, dict) else None


def verifies(vc: dict, keys: dict) -> bool:
    from nacl.signing import VerifyKey
    from app.crypto.hybrid import verify_proof

    proof = first_proof(vc)
    if proof is None:
        return False
    key = keys.get(proof.get("verificationMethod", ""))
    if key is None:
        return False
    try:
        return bool(verify_proof(vc, VerifyKey(key)).get("valid"))
    except Exception:
        return False


def failing_ids(rows, keys: dict, exempt: frozenset) -> list[int]:
    return sorted(cid for cid, vc in rows
                  if cid not in exempt and not verifies(vc, keys))


def _fetch_did_document(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - fixed https URL
        return json.load(r)


def _rows_and_marked():
    import psycopg2

    conn = psycopg2.connect(host="localhost", dbname="moltstack", user="moltstack",
                            password=os.environ.get("MOLTSTACK_DB_PW"))
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, raw_vc FROM credentials WHERE raw_vc IS NOT NULL ORDER BY id")
        rows = [(cid, vc if isinstance(vc, dict) else json.loads(vc)) for cid, vc in cur.fetchall()]
        cur.execute("SELECT to_regclass('credential_verification_status') IS NOT NULL")
        marked = set()
        if cur.fetchone()[0]:
            cur.execute("SELECT credential_id FROM credential_verification_status")
            marked = {r[0] for r in cur.fetchall()}
        return rows, marked
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print the failing ids")
    ap.add_argument("--did-document", default=DID_DOCUMENT_URL)
    args = ap.parse_args(argv)
    try:
        keys = keys_from_did_document(_fetch_did_document(args.did_document))
    except Exception as exc:
        print(f"UNREADABLE did document: {type(exc).__name__}")
        return 0
    if not keys:
        print("UNREADABLE did document carries no Ed25519 key")
        return 0
    try:
        rows, marked = _rows_and_marked()
    except Exception as exc:
        print(f"UNREADABLE database: {type(exc).__name__}")
        return 0
    if not rows:
        print("UNREADABLE no stored credentials read")
        return 0
    bad = failing_ids(rows, keys, KNOWN_UNVERIFIABLE | frozenset(marked))
    if args.list:
        print(f"checked {len(rows)}, exempt {len(KNOWN_UNVERIFIABLE | set(marked))}, failing {len(bad)}")
        for cid in bad:
            print(cid)
    print(len(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
