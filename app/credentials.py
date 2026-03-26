"""MolTrust Verifiable Credentials - W3C VC Data Model"""
import os, json, datetime, hashlib
from nacl.signing import SigningKey
from app.crypto.kms_signer import get_decrypted_signing_key_hex

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
    payload = json.dumps(credential, sort_keys=True).encode()
    signed = signing_key.sign(payload)

    credential["proof"] = {
        "type": "Ed25519Signature2020",
        "created": now.isoformat() + "Z",
        "verificationMethod": f"{ISSUER_DID}#key-1",
        "proofPurpose": "assertionMethod",
        "proofValue": signed.signature.hex()
    }
    return credential

def verify_credential(credential: dict) -> dict:
    proof = credential.get("proof")
    if not proof:
        return {"valid": False, "error": "No proof found"}
    if proof.get("verificationMethod") != f"{ISSUER_DID}#key-1":
        return {"valid": False, "error": "Unknown verification method"}

    try:
        cred_copy = {k: v for k, v in credential.items() if k != "proof"}
        payload = json.dumps(cred_copy, sort_keys=True).encode()
        signature = bytes.fromhex(proof["proofValue"])

        signing_key = get_signing_key()
        verify_key = signing_key.verify_key
        verify_key.verify(payload, signature)

        exp = credential.get("expirationDate", "")
        if exp:
            exp_dt = datetime.datetime.fromisoformat(exp.replace("Z", ""))
            if datetime.datetime.utcnow() > exp_dt:
                return {"valid": False, "error": "Credential expired"}

        return {"valid": True, "issuer": credential["issuer"], "subject": credential["credentialSubject"]["id"]}
    except Exception as e:
        return {"valid": False, "error": str(e)}
