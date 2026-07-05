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


def verify_credential(credential: dict) -> dict:
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
