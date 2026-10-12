"""Remove `evidence` from every stored credential, and prove the run closed two gaps.

From 20.09. to 12.10.2026 the anchor batch wrote `evidence` into
credentials.raw_vc after signing (#363). The signature does not cover it, so no
stored credential verified as stored. Two other copies of the same documents
drifted with it: the 67 documents pinned to IPFS at issuance, and the
conformity commitment `sha256(json.dumps(vc, sort_keys=True))` returned by
/compliance/declaration (app/main.py). Both were computed over the document as
signed, without `evidence`.

The run is one transaction and aborts on anything it cannot account for:

  1. Precondition: the deployed anchor.py no longer writes `evidence`
     (otherwise the next batch, every two hours, puts it back).
  2. Backup of the table with pg_dump, 0600, sha256 in the log.
  3. Before: count signature, IPFS and commitment agreement. Logged.
  4. Lock the rows, drop `evidence`, verify each row against the public key its
     verificationMethod names in did.json. Any failure outside the 14 known ids
     rolls back.
  5. After, inside the same transaction: no row carries `evidence`; every row
     but the 14 verifies; every readable IPFS document equals the stored one;
     every conformity commitment equals the one over the signed form; every
     leaf recomputed from the columns equals the stored leaf. Any miss rolls
     back. Only then COMMIT.

Default is a dry run that does everything but the UPDATE and the COMMIT.
`--apply` writes. Prints ids, counts and hashes, never a document.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess  # nosec B404 - pg_dump with fixed arguments
import sys
import time
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nacl.signing import VerifyKey  # noqa: E402

from app.crypto.hybrid import verify_proof  # noqa: E402
from app.provenance.anchor import compute_credential_leaf  # noqa: E402

DID_DOC_URL = "https://api.moltrust.ch/.well-known/did.json"
IPFS_GATEWAY = "https://gateway.pinata.cloud/ipfs"
DEPLOYED_ANCHOR = "/home/moltstack/moltstack/app/provenance/anchor.py"
LOG = os.path.expanduser("~/logs/migration-2026-10-12-drop-evidence.jsonl")
BACKUP_DIR = os.path.expanduser("~/backups")
CONFORMITY_TYPE = "MolTrustConformityDeclaration"

# Verify in no tested canonicalization against the DID key (check of
# 12.10.2026, ~/Downloads/block-1012/credential-pruefung.md). `evidence` is
# removed from them as well; whether they verify is not this run's to decide.
KNOWN_UNVERIFIABLE = frozenset({925, 926, 927, 928, 929, 930, 931, 932, 933,
                                934, 935, 936, 937, 940})

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class Abort(Exception):
    """A condition the run cannot account for. Nothing is written."""


# --- pure parts --------------------------------------------------------------

def keys_from_did_doc(doc: dict) -> dict[str, VerifyKey]:
    keys = {}
    for vm in doc.get("verificationMethod") or []:
        mb = vm.get("publicKeyMultibase") or ""
        if vm.get("type") != "Ed25519VerificationKey2020" or not mb.startswith("z"):
            continue
        n = 0
        for ch in mb[1:]:
            n = n * 58 + _B58.index(ch)
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        if raw[:2] == b"\xed\x01" and len(raw) == 34:
            keys[vm["id"]] = VerifyKey(raw[2:])
    return keys


def without_evidence(vc: dict) -> dict:
    return {k: v for k, v in vc.items() if k != "evidence"}


def verifies(vc: dict, keys: dict[str, VerifyKey]) -> bool:
    proofs = vc.get("proof")
    proofs = proofs if isinstance(proofs, list) else [proofs]
    method = next((p.get("verificationMethod") for p in proofs
                   if isinstance(p, dict) and p.get("type") == "Ed25519Signature2020"), None)
    key = keys.get(method or "")
    return key is not None and verify_proof(vc, key).get("valid") is True


def commitment(vc: dict) -> str:
    """The conformity commitment exactly as app/main.py computes it."""
    return hashlib.sha256(json.dumps(vc, sort_keys=True).encode()).hexdigest()


def signature_counts(rows, keys) -> dict:
    as_stored = sum(verifies(vc, keys) for _, vc in rows)
    return {"rows": len(rows), "with_evidence": sum("evidence" in vc for _, vc in rows),
            "verify_as_stored": as_stored}


def ipfs_counts(rows, pinned: dict) -> dict:
    """pinned: id -> pinned document. Rows without a pin are not counted."""
    eq = ne = 0
    differing = []
    for cid, vc in rows:
        if cid not in pinned:
            continue
        if pinned[cid] == vc:
            eq += 1
        else:
            ne += 1
            differing.append(cid)
    return {"pinned": eq + ne, "equal_to_stored": eq, "differ": ne, "differing_ids": differing[:20]}


def conformity_counts(rows, types: dict) -> dict:
    """Commitment over the stored document against the one over the signed form."""
    eq = ne = 0
    for cid, vc in rows:
        if types.get(cid) != CONFORMITY_TYPE:
            continue
        if commitment(vc) == commitment(without_evidence(vc)):
            eq += 1
        else:
            ne += 1
    return {"declarations": eq + ne, "equal_to_signed_form": eq, "differ": ne}


def plan(rows, keys, known=KNOWN_UNVERIFIABLE) -> tuple[list[int], list[int], list[int]]:
    """(ids to update, known ids that still fail, unknown ids that fail)."""
    update, known_bad, bad = [], [], []
    for cid, vc in rows:
        if "evidence" not in vc:
            continue
        update.append(cid)
        if not verifies(without_evidence(vc), keys):
            (known_bad if cid in known else bad).append(cid)
    return update, known_bad, bad


# --- the run -----------------------------------------------------------------

def log(event: str, **kw) -> None:
    line = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "event": event, **kw}
    print(json.dumps(line))
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(line) + "\n")


def fetch_json(url: str, tries: int = 4) -> dict:
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=40) as r:  # nosec B310
                return json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 - reported below
            last = exc
            time.sleep(5 * (attempt + 1))
    raise Abort(f"unreadable {url.rsplit('/', 1)[-1]}: {type(last).__name__}")


def backup() -> None:
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"credentials-before-drop-evidence-"
                        f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.sql")
    old = os.umask(0o077)
    try:
        with open(path, "wb") as f:
            p = subprocess.run(["pg_dump", "-h", "localhost", "-U", "moltstack",  # nosec B603 B607
                                "-t", "public.credentials", "moltstack"], stdout=f)
    finally:
        os.umask(old)
    if p.returncode:
        raise Abort(f"pg_dump exit {p.returncode}")
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    log("backup", path=path, sha256=digest, bytes=os.path.getsize(path))


def run(apply: bool) -> int:
    with open(DEPLOYED_ANCHOR) as f:
        if "'{evidence}'" in f.read():
            raise Abort("deployed anchor.py still writes evidence; deploy part 1 first")
    keys = keys_from_did_doc(fetch_json(DID_DOC_URL))
    if not keys:
        raise Abort("did.json carries no Ed25519 key")

    import psycopg2
    con = psycopg2.connect(host="localhost", dbname="moltstack", user="moltstack",
                           password=os.environ.get("MOLTSTACK_DB_PW") or None)
    cur = con.cursor()

    # IPFS documents do not change; read them before any lock is taken.
    cur.execute("SELECT id, ipfs_cid FROM credentials WHERE ipfs_cid IS NOT NULL ORDER BY id")
    pins = cur.fetchall()
    pinned = {}
    for cid, ipfs in pins:
        pinned[cid] = fetch_json(f"{IPFS_GATEWAY}/{ipfs}")
        time.sleep(1.2)
    log("ipfs_read", rows_with_cid=len(pins), read=len(pinned))
    con.rollback()

    if apply:
        backup()

    cur.execute("SELECT count(*) FROM credentials WHERE raw_vc IS NOT NULL")
    total = cur.fetchone()[0]
    cur.execute("""SELECT id, credential_type, raw_vc FROM credentials
                    WHERE raw_vc IS NOT NULL ORDER BY id FOR UPDATE""")
    locked = cur.fetchall()
    if len(locked) != total:
        raise Abort(f"read {len(locked)} of {total} rows")
    types = {cid: t for cid, t, _ in locked}
    rows = [(cid, vc) for cid, _, vc in locked]

    before = {"signature": signature_counts(rows, keys),
              "ipfs": ipfs_counts(rows, pinned),
              "conformity": conformity_counts(rows, types)}
    log("before", **before)

    update, known_bad, bad = plan(rows, keys)
    log("plan", to_update=len(update), known_unverifiable=sorted(known_bad), unknown_failures=sorted(bad))
    if bad:
        con.rollback()
        raise Abort(f"{len(bad)} rows outside the known 14 fail after removing evidence: {sorted(bad)[:20]}")

    cur.execute("UPDATE credentials SET raw_vc = raw_vc - 'evidence' WHERE id = ANY(%s)", (update,))
    if cur.rowcount != len(update):
        con.rollback()
        raise Abort(f"updated {cur.rowcount} of {len(update)}")

    cur.execute("SELECT id, raw_vc FROM credentials WHERE raw_vc IS NOT NULL ORDER BY id")
    after_rows = cur.fetchall()
    after = {"signature": signature_counts(after_rows, keys),
             "ipfs": ipfs_counts(after_rows, pinned),
             "conformity": conformity_counts(after_rows, types)}
    log("after", **after)

    cur.execute("""SELECT c.id, c.subject_did, c.credential_type, c.issued_at, c.proof_value,
                          a.merkle_proof->>'leaf'
                     FROM credentials c JOIN credential_anchors a ON a.credential_id = c.id""")
    leaf_miss = [r[0] for r in cur.fetchall()
                 if compute_credential_leaf(str(r[0]), r[1], r[2], r[3].isoformat(), r[4] or "") != r[5]]

    unverified = after["signature"]["rows"] - after["signature"]["verify_as_stored"]
    problems = []
    if after["signature"]["with_evidence"]:
        problems.append(f"{after['signature']['with_evidence']} rows still carry evidence")
    if unverified != len(KNOWN_UNVERIFIABLE & set(types)):
        problems.append(f"{unverified} rows do not verify, expected {len(KNOWN_UNVERIFIABLE & set(types))}")
    if after["ipfs"]["differ"]:
        problems.append(f"{after['ipfs']['differ']} IPFS documents differ: {after['ipfs']['differing_ids']}")
    if after["conformity"]["differ"]:
        problems.append(f"{after['conformity']['differ']} conformity commitments differ")
    if leaf_miss:
        problems.append(f"{len(leaf_miss)} leaves no longer match: {leaf_miss[:20]}")
    if problems:
        con.rollback()
        raise Abort("; ".join(problems))

    if not apply:
        con.rollback()
        log("dry_run_ok", would_update=len(update))
        return 0
    con.commit()
    log("committed", updated=len(update))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = ap.parse_args()
    try:
        return run(args.apply)
    except Abort as exc:
        log("abort", reason=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
