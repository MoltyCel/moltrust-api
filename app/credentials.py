"""MolTrust Verifiable Credentials - W3C VC Data Model v2.0

Issuance: emits W3C VC Data Model v2 only (validFrom/validUntil, v2 @context).
Verification: dual-accept — recognises v2 (validFrom/validUntil) AND legacy
v1 (issuanceDate/expirationDate) so previously-issued credentials still
verify until the dataset is fully rotated.

Signing: dual-signature (Ed25519 + ML-DSA-65/Dilithium3) when Dilithium keys
are configured, Ed25519-only otherwise. JCS (RFC 8785) canonicalization.
See app/crypto/hybrid.py for the composite-signature verification contract.
"""
import os, json, datetime, hashlib
from nacl.signing import SigningKey
from app.crypto.kms_signer import get_decrypted_signing_key_hex
from app.crypto.hybrid import dual_sign, verify_proof

ISSUER_DID = "did:web:api.moltrust.ch"

VC_V2_CONTEXT = "https://www.w3.org/ns/credentials/v2"
VC_V1_CONTEXT = "https://www.w3.org/2018/credentials/v1"
MOLTRUST_CONTEXT = "https://api.moltrust.ch/contexts/trust/v1"


def get_signing_key():
    hex_key = get_decrypted_signing_key_hex()
    return SigningKey(bytes.fromhex(hex_key))


def vc_valid_from(vc: dict) -> str:
    """Return the issuance instant — `validFrom` (v2), falling back to `issuanceDate` (v1)."""
    return vc.get("validFrom") or vc.get("issuanceDate", "") or ""


def vc_valid_until(vc: dict) -> str:
    """Return the expiry instant — `validUntil` (v2), falling back to `expirationDate` (v1)."""
    return vc.get("validUntil") or vc.get("expirationDate", "") or ""


def issue_credential(subject_did: str, credential_type: str, claims: dict) -> dict:
    now = datetime.datetime.utcnow()
    credential = {
        "@context": [
            VC_V2_CONTEXT,
            MOLTRUST_CONTEXT,
        ],
        "type": ["VerifiableCredential", credential_type],
        "issuer": ISSUER_DID,
        "validFrom": now.isoformat() + "Z",
        "validUntil": (now + datetime.timedelta(days=365)).isoformat() + "Z",
        "credentialSubject": {
            "id": subject_did,
            **claims,
        },
    }

    signing_key = get_signing_key()
    credential = dual_sign(credential, signing_key)
    return credential


#: Fields the answer of POST /credentials/issue carries next to the signed
#: credential, added after signing: `ipfs_cid`/`ipfs_url` by the IPFS publish
#: in issue_and_store_vc, `renew_at` by the renewal middleware. No proof covers
#: them (ops VC-1).
ISSUE_ANSWER_FIELDS = ("renew_at", "ipfs_cid", "ipfs_url")


def verify_credential(credential: dict) -> dict:
    """Verify a credential as presented, or as the issue answer handed it out.

    The credential is checked as it stands first. Only if that fails and it
    carries one of ISSUE_ANSWER_FIELDS is it checked once more without them;
    a pass then lists them under `unsigned_fields`, so nobody reads them as
    signed. A credential whose proof covers one of the fields verifies on the
    first check and keeps it signed. Any other added field still fails.
    """
    result = _verify_signed(credential)
    if result["valid"] or not isinstance(credential, dict):
        return {**result, "unsigned_fields": []}
    added = sorted(k for k in ISSUE_ANSWER_FIELDS if k in credential)
    if not added:
        return {**result, "unsigned_fields": []}
    retry = _verify_signed({k: v for k, v in credential.items() if k not in added})
    if not retry["valid"]:
        return {**result, "unsigned_fields": []}
    return {**retry, "unsigned_fields": added}


def _verify_signed(credential: dict) -> dict:
    # Input validation: malformed inputs must not raise (would cause 500).
    if not isinstance(credential, dict):
        return {"valid": False, "error": "credential is not a dict"}

    proof = credential.get("proof")
    if not proof:
        return {"valid": False, "error": "No proof found"}

    # Normalize proof to a list for uniform handling.
    if isinstance(proof, list):
        proofs = proof
    elif isinstance(proof, dict):
        proofs = [proof]
    else:
        return {"valid": False, "error": "proof is not a dict or list"}

    # Every proof's verificationMethod must belong to our issuer.
    for p in proofs:
        if not isinstance(p, dict):
            return {"valid": False, "error": "proof entry is not a dict"}
        vm = p.get("verificationMethod")
        if not isinstance(vm, str) or not vm.startswith(ISSUER_DID):
            return {"valid": False, "error": f"Unknown verification method: {vm!r}"}

    try:
        signing_key = get_signing_key()
        verify_key = signing_key.verify_key

        result = verify_proof(credential, verify_key)
        if not result["valid"]:
            # verify_proof may set an explicit error (e.g. PQC policy violation,
            # "No proof found", "credential is not a dict") without a checks
            # array. Prefer that error; fall back to aggregating check errors.
            if result.get("error"):
                error = result["error"]
            else:
                error = "; ".join(
                    c.get("error", "check failed")
                    for c in result.get("checks", []) if not c.get("valid")
                )
            return {"valid": False, "error": error,
                    "checks": result.get("checks", []),
                    "pqc_policy": result.get("pqc_policy")}

        exp = vc_valid_until(credential)
        if exp:
            exp_dt = datetime.datetime.fromisoformat(exp.replace("Z", ""))
            if datetime.datetime.utcnow() > exp_dt:
                return {"valid": False, "error": "Credential expired"}

        return {
            "valid": True,
            "issuer": credential["issuer"],
            "subject": credential["credentialSubject"]["id"],
            "checks": result.get("checks", []),
            "pqc_policy": result.get("pqc_policy"),
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}
