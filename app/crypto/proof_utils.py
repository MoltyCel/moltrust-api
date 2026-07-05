"""
Proof-field helpers for MolTrust credentials.

A credential's `proof` may be a single proof object (legacy / Ed25519-only)
or a list of proofs (dual-signature: Ed25519 + Dilithium). All DB-insert and
verification sites must use these helpers instead of indexing `proof[0]`,
which was flagged by the security review as a downgrade bug: a hardcoded
[0] can persist a Dilithium proofValue into a column that callers read as
the Ed25519 proof.

Proof types referenced here:
  - "Ed25519Signature2020"            -> Ed25519 leg
  - "DilithiumSignature2026"          -> ML-DSA-65 leg
"""
from typing import Optional

ED25519_PROOF_TYPE = "Ed25519Signature2020"
DILITHIUM_PROOF_TYPE = "DilithiumSignature2026"


def _as_proof_list(proof) -> list[dict]:
    """Normalise the `proof` field into a list of proof dicts.

    Non-dict/non-list inputs (string, int, None, etc.) yield an empty list
    so downstream code can treat a malformed proof as "no valid proofs"
    rather than raising.
    """
    if proof is None:
        return []
    if isinstance(proof, list):
        return [p for p in proof if isinstance(p, dict)]
    if isinstance(proof, dict):
        return [proof]
    return []


def get_proofs(credential: dict) -> list[dict]:
    """Return all proof dicts from a credential (handles single/list/missing).

    Non-dict credentials yield an empty list rather than raising.
    """
    if not isinstance(credential, dict):
        return []
    return _as_proof_list(credential.get("proof"))


def find_proof(credential: dict, proof_type: str) -> Optional[dict]:
    """Return the first proof whose `type` matches exactly, or None.

    Exact equality only. Substring matching was removed because it is a
    latent type-confusion bug: a declared type like "EvilEd25519NotReally"
    would match a caller searching for "Ed25519Signature2020".
    """
    for p in get_proofs(credential):
        ptype = p.get("type", "")
        if not isinstance(ptype, str):
            continue
        if ptype == proof_type:
            return p
    return None


def get_ed25519_proof(credential: dict) -> Optional[dict]:
    """Return the Ed25519 proof, or None."""
    return find_proof(credential, ED25519_PROOF_TYPE)


def get_dilithium_proof(credential: dict) -> Optional[dict]:
    """Return the Dilithium proof, or None."""
    return find_proof(credential, DILITHIUM_PROOF_TYPE)


def get_primary_proof_value(credential: dict) -> str:
    """Return the canonical proofValue to persist in the credentials table.

    The credentials.proof_value column historically holds the Ed25519
    signature. With dual signatures we keep that contract: the Ed25519
    proofValue is the primary persisted value. If only a non-Ed25519 proof
    is present we return its value rather than crashing, but that path
    indicates a credential shape no current issuer produces.

    Raises KeyError if no proof at all is present (caller should not be
    inserting a credential without a proof).
    """
    ed = get_ed25519_proof(credential)
    if ed is not None and "proofValue" in ed:
        pv = ed["proofValue"]
        if isinstance(pv, str):
            return pv
    proofs = get_proofs(credential)
    if proofs and "proofValue" in proofs[0]:
        pv = proofs[0]["proofValue"]
        if isinstance(pv, str):
            return pv
    raise KeyError("credential has no proofValue to persist")


def has_dual_signature(credential: dict) -> bool:
    """True iff the credential carries both Ed25519 and Dilithium proofs."""
    return get_ed25519_proof(credential) is not None and \
        get_dilithium_proof(credential) is not None