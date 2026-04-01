"""
Hybrid (dual) signature module for MolTrust.

Produces credentials with both Ed25519 and Dilithium3 proofs:
- Ed25519: legacy, for verifiers that don't support PQC yet
- Dilithium3: quantum-safe, the primary proof going forward

If Dilithium is not configured, falls back to Ed25519-only signing.
This allows a gradual rollout: deploy the code first, add Dilithium
keys when ready.
"""
import json
import logging
from app.crypto import dilithium

logger = logging.getLogger("moltrust.crypto.hybrid")

ISSUER_DID = "did:web:api.moltrust.ch"


def dual_sign(credential: dict, ed25519_key) -> dict:
    """Sign a credential with Ed25519 and optionally Dilithium3.

    Args:
        credential: The VC dict (without proof field)
        ed25519_key: nacl.signing.SigningKey instance

    Returns:
        The credential dict with proof field set (single proof or array of two)
    """
    try:
        import jcs
        payload = jcs.canonicalize(credential)
    except ImportError:
        payload = json.dumps(credential, sort_keys=True).encode()

    now_str = credential.get("issuanceDate", "")

    # Ed25519 signature
    ed_signed = ed25519_key.sign(payload)
    ed_proof = {
        "type": "Ed25519Signature2020",
        "created": now_str,
        "verificationMethod": f"{ISSUER_DID}#key-ed25519",
        "proofPurpose": "assertionMethod",
        "canonicalizationAlgorithm": "JCS",
        "proofValue": ed_signed.signature.hex(),
    }

    # Dilithium signature (if available)
    dil_sig = dilithium.sign(payload)
    if dil_sig is not None:
        dil_proof = {
            "type": "DilithiumSignature2026",
            "created": now_str,
            "verificationMethod": f"{ISSUER_DID}#key-dilithium",
            "proofPurpose": "assertionMethod",
            "canonicalizationAlgorithm": "JCS",
            "proofValue": dil_sig.hex(),
        }
        credential["proof"] = [ed_proof, dil_proof]
        logger.info("Credential dual-signed (Ed25519 + Dilithium3)")
    else:
        credential["proof"] = ed_proof
        logger.debug("Credential signed with Ed25519 only (Dilithium not configured)")

    return credential


def verify_proof(credential: dict, ed25519_verify_key) -> dict:
    """Verify a credential's proof(s).

    Supports:
    - Single Ed25519 proof (legacy, key-1 or key-ed25519)
    - Single Dilithium proof
    - Dual proof array (verifies both)

    Returns dict with valid, checks, and errors.
    """
    proof = credential.get("proof")
    if not proof:
        return {"valid": False, "error": "No proof found"}

    cred_copy = {k: v for k, v in credential.items() if k != "proof"}

    proofs = proof if isinstance(proof, list) else [proof]
    results = {"valid": True, "checks": []}

    for p in proofs:
        proof_type = p.get("type", "")
        vm = p.get("verificationMethod", "")

        # Determine canonicalization
        if p.get("canonicalizationAlgorithm") == "JCS":
            try:
                import jcs
                payload = jcs.canonicalize(cred_copy)
            except ImportError:
                results["checks"].append({"type": proof_type, "valid": False, "error": "JCS library not available"})
                results["valid"] = False
                continue
        else:
            payload = json.dumps(cred_copy, sort_keys=True).encode()

        try:
            signature = bytes.fromhex(p["proofValue"])
        except (ValueError, KeyError) as e:
            results["checks"].append({"type": proof_type, "valid": False, "error": str(e)})
            results["valid"] = False
            continue

        if "Ed25519" in proof_type:
            try:
                ed25519_verify_key.verify(payload, signature)
                results["checks"].append({"type": "Ed25519", "valid": True})
            except Exception as e:
                results["checks"].append({"type": "Ed25519", "valid": False, "error": str(e)})
                results["valid"] = False

        elif "Dilithium" in proof_type:
            pk_hex = dilithium.get_public_key_hex()
            if not pk_hex:
                results["checks"].append({"type": "Dilithium", "valid": False, "error": "Dilithium public key not configured"})
                results["valid"] = False
            else:
                ok = dilithium.verify(payload, signature, bytes.fromhex(pk_hex))
                results["checks"].append({"type": "Dilithium", "valid": ok})
                if not ok:
                    results["valid"] = False
        else:
            results["checks"].append({"type": proof_type, "valid": False, "error": f"Unknown proof type: {proof_type}"})
            results["valid"] = False

    return results
