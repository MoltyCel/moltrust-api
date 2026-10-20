"""Our own archive of every credential we issue (ops IP-1).

Until IP-1 the full signed credential went to IPFS on POST /credentials/issue,
and that public copy was the only one kept outside the issuing table. From IP-1
on the full credential is written to `vc_archive` in our own database, and IPFS
receives only its hash (`hash_document`). CIDs published before IP-1 stay as
they are; nothing here unpins or deletes anything.

The hash is SHA-256 over the RFC 8785 (JCS) form of the credential as signed,
before any answer field (`ipfs_cid`, `ipfs_url`, `renew_at`) is added. Anyone
holding the credential can recompute it and compare it with the pinned
document.

Writing the archive never blocks issuance: a failed write is logged and the
credential goes out as before. The invariant c-vc-archive-complete counts
credentials that were issued without an archive row.
"""
from __future__ import annotations

import hashlib
import json
import logging

from app.signature import canonicalize

log = logging.getLogger("moltrust.vc_archive")

HASH_ALG = "sha256-jcs"


def vc_hash(vc: dict) -> str:
    """Hex SHA-256 over the JCS form of the credential."""
    return hashlib.sha256(canonicalize(vc)).hexdigest()


def hash_document(vc: dict) -> dict:
    """What goes to IPFS for a credential: its hash and how it was computed."""
    return {"vc_hash": vc_hash(vc), "hash_alg": HASH_ALG}


def _credential_type(vc: dict) -> str | None:
    types = vc.get("type")
    if isinstance(types, list) and types:
        return str(types[-1])[:64]
    return str(types)[:64] if types else None


def _subject(vc: dict) -> str | None:
    subject = vc.get("credentialSubject")
    if isinstance(subject, dict) and subject.get("id"):
        return str(subject["id"])[:128]
    return None


def _proof_value(vc: dict) -> str | None:
    try:
        from app.crypto.proof_utils import get_primary_proof_value
        return get_primary_proof_value(vc) or None
    except Exception:  # noqa: BLE001 - an unsigned credential has none
        return None


def row(vc: dict, source: str, *, ipfs_cid: str | None = None,
        origin: str = "issued") -> tuple:
    """The values of one vc_archive row, in INSERT_SQL order."""
    issuer = vc.get("issuer")
    if isinstance(issuer, dict):
        issuer = issuer.get("id")
    return (vc_hash(vc), _credential_type(vc), _subject(vc),
            str(issuer)[:100] if issuer else None, _proof_value(vc),
            json.dumps(vc), source[:64], origin, ipfs_cid)


INSERT_SQL = (
    "INSERT INTO vc_archive (vc_hash, credential_type, subject_did, issuer, "
    "proof_value, raw_vc, source, origin, ipfs_cid) "
    "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9) "
    "ON CONFLICT (vc_hash) DO NOTHING"
)


async def archive(conn, vc: dict, source: str, *, ipfs_cid: str | None = None) -> bool:
    """Write one issued credential to the archive. True if the write went through.

    Runs in its own transaction, or in a savepoint when the caller already has
    one open, so a failed write cannot abort the caller's transaction.
    """
    try:
        values = row(vc, source, ipfs_cid=ipfs_cid)
        async with conn.transaction():
            await conn.execute(INSERT_SQL, *values)
        return True
    except Exception as e:  # noqa: BLE001 - archiving never blocks issuance
        log.warning("VC archive write failed (%s): %s", source, type(e).__name__)
        return False
