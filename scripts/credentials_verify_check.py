"""Count stored credentials that do not verify against their own verificationMethod.

The key comes from the published DID document, the way an outside verifier
would get it, not from the signing key: a check that verifies with the key in
force today stays green after a rotation that leaves every older credential
unverifiable. Output for the selftest: a reason line, then the count.

Read-only. Prints ids and counts, never a document.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nacl.signing import VerifyKey  # noqa: E402

from app.crypto.hybrid import verify_proof  # noqa: E402

DID_DOC_URL = "https://api.moltrust.ch/.well-known/did.json"

# Fourteen #key-1 credentials, issued 28.02. to 23.03.2026, verify in no tested
# canonicalization against the DID key, with or without `evidence` (check of
# 12.10.2026, ~/Downloads/block-1012/credential-pruefung.md). Nine predate the
# first commit in this repo, so the code that signed them is not on record.
# They are listed here so the check measures everything else; they are not
# counted as valid anywhere.
KNOWN_UNVERIFIABLE = frozenset({925, 926, 927, 928, 929, 930, 931, 932, 933,
                                934, 935, 936, 937, 940})

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ED25519_PUB_PREFIX = b"\xed\x01"


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def keys_from_did_doc(doc: dict) -> dict[str, VerifyKey]:
    """verificationMethod id -> Ed25519 verify key, for every Ed25519 method."""
    keys = {}
    for vm in doc.get("verificationMethod") or []:
        mb = vm.get("publicKeyMultibase") or ""
        if vm.get("type") != "Ed25519VerificationKey2020" or not mb.startswith("z"):
            continue
        raw = _b58decode(mb[1:])
        if raw[:2] != _ED25519_PUB_PREFIX or len(raw) != 34:
            continue
        keys[vm["id"]] = VerifyKey(raw[2:])
    return keys


def _method(vc: dict) -> str | None:
    proofs = vc.get("proof")
    proofs = proofs if isinstance(proofs, list) else [proofs]
    for p in proofs:
        if isinstance(p, dict) and p.get("type") == "Ed25519Signature2020":
            return p.get("verificationMethod")
    return None


def check(rows, keys: dict[str, VerifyKey], known=KNOWN_UNVERIFIABLE) -> dict:
    """rows: (id, raw_vc dict). Returns counts and the failing ids."""
    bad, with_evidence, skipped_known = [], 0, 0
    for cid, vc in rows:
        if cid in known:
            skipped_known += 1
            continue
        key = keys.get(_method(vc) or "")
        ok = key is not None and verify_proof(vc, key).get("valid") is True
        if not ok:
            bad.append(cid)
            with_evidence += "evidence" in vc
    return {"checked": len(rows), "known": skipped_known, "bad": bad,
            "bad_with_evidence": with_evidence}


def main() -> int:
    try:
        with urllib.request.urlopen(DID_DOC_URL, timeout=20) as r:  # nosec B310
            keys = keys_from_did_doc(json.loads(r.read()))
    except Exception as exc:
        print(f"UNREADABLE: did.json {type(exc).__name__}")
        return 0
    if not keys:
        print("UNREADABLE: did.json traegt keinen Ed25519-Schluessel")
        return 0
    try:
        import psycopg2
        con = psycopg2.connect(host="localhost", dbname="moltstack", user="moltstack",
                               password=os.environ.get("MOLTSTACK_DB_PW") or None)
        cur = con.cursor()
        cur.execute("SELECT count(*) FROM credentials WHERE raw_vc IS NOT NULL")
        total = cur.fetchone()[0]
        cur.execute("SELECT id, raw_vc FROM credentials WHERE raw_vc IS NOT NULL ORDER BY id")
        rows = cur.fetchall()
        con.close()
    except Exception as exc:
        print(f"UNREADABLE: credentials {type(exc).__name__}")
        return 0
    if len(rows) != total:
        print(f"UNREADABLE: {len(rows)} von {total} Zeilen gelesen")
        return 0
    res = check(rows, keys)
    first = ", ".join(str(i) for i in res["bad"][:10])
    print(f"{res['checked']} gespeichert, {res['known']} bekannt nicht pruefbar ausgenommen, "
          f"{len(res['bad'])} pruefen nicht gegen ihren verificationMethod"
          + (f" (davon {res['bad_with_evidence']} mit evidence; erste ids {first})" if res["bad"] else ""))
    print(len(res["bad"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
