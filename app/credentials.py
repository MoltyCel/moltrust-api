"""MolTrust Verifiable Credentials - W3C VC Data Model

Supports dual signatures (Ed25519 + Dilithium/ML-DSA-65) for post-quantum safety.
If Dilithium keys are not configured, falls back to Ed25519-only signing.
Verification handles legacy (single Ed25519), new (dual), and future
(Dilithium-only) credentials transparently.
"""
import os, json, datetime, hashlib
import jcs
from nacl.signing import SigningKey
from app.crypto.kms_signer import get_decrypted_signing_key_hex
from app.crypto.hybrid import dual_sign, verify_proof

ISSUER_DID = "did:web:api.moltrust.ch"

def get_signing_key():
    hex_key = get_decrypted_signing_key_hex()
    return SigningKey(bytes.fromhex(hex_key))

def issue_credential(subject_did: str, credential_type: str, claims: dict) -> dict:
    now = datetime.datetime.utcnow()
    credential = {
        "@context": [
            "https://www.w3.org/2018/credentials/v1",
            "https://api.moltrust.ch/contexts/trust/v1"
        ],
        "type": ["VerifiableCredential", credential_type],
        "issuer": ISSUER_DID,
        "issuanceDate": now.isoformat() + "Z",
        "expirationDate": (now + datetime.timedelta(days=365)).isoformat() + "Z",
        "credentialSubject": {
            "id": subject_did,
            **claims
        }
    }

    signing_key = get_signing_key()
    credential = dual_sign(credential, signing_key)
    return credential

def verify_credential(credential: dict) -> dict:
    proof = credential.get("proof")
    if not proof:
        return {"valid": False, "error": "No proof found"}

    # Check verification method(s) belong to our issuer
    proofs = proof if isinstance(proof, list) else [proof]
    for p in proofs:
        vm = p.get("verificationMethod", "")
        if not vm.startswith(ISSUER_DID):
            return {"valid": False, "error": f"Unknown verification method: {vm}"}

    try:
        signing_key = get_signing_key()
        verify_key = signing_key.verify_key

        result = verify_proof(credential, verify_key)
        if not result["valid"]:
            errors = [c.get("error", "check failed") for c in result.get("checks", []) if not c.get("valid")]
            return {"valid": False, "error": "; ".join(errors), "checks": result["checks"]}

        # Check expiration
        exp = credential.get("expirationDate", "")
        if exp:
            exp_dt = datetime.datetime.fromisoformat(exp.replace("Z", ""))
            if datetime.datetime.utcnow() > exp_dt:
                return {"valid": False, "error": "Credential expired"}

        return {
            "valid": True,
            "issuer": credential["issuer"],
            "subject": credential["credentialSubject"]["id"],
            "checks": result["checks"],
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}
