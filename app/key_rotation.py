"""Key rotation for did:moltrust, method specification section 4.3.

Section 4.3: the agent submits a signed update request with a new Ed25519 key;
the old key stays in the DID document marked "revoked": true with a
revokedDate. The section names "a signature over rotation payload" and does not
define the payload. The one defined here is a proposal that the specification
change accompanying this code puts forward, and it is open until that lands:

    moltrust-rotate-key/v1 LF <did> LF <current public key hex> LF <new public key hex>

signed with the CURRENT key, Ed25519, base64url without padding. Naming the
current key binds the signature to one step of the history: once the key has
moved on, a captured request no longer verifies, so it cannot be replayed.

Pure helpers with no app or db imports, so they test in isolation.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PAYLOAD_TAG = "moltrust-rotate-key/v1"


def decode_public_key(value: str) -> str:
    """An Ed25519 public key as 64 lowercase hex.

    Section 4.3 writes the new key as base64url; /identity/register-pop takes
    64 hex. Both are accepted here and the stored form is hex, the same as
    agents.public_key_hex.
    """
    v = (value or "").strip()
    if len(v) == 64:
        try:
            raw = bytes.fromhex(v)
        except ValueError:
            raw = b""
        if len(raw) == 32:
            return raw.hex()
    try:
        raw = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) == 32:
        return raw.hex()
    raise ValueError(
        "new_public_key must be an Ed25519 public key: 32 bytes as base64url "
        "(section 4.3) or as 64 hex characters."
    )


def rotation_payload(did: str, current_hex: str, new_hex: str) -> bytes:
    return "\n".join([PAYLOAD_TAG, did, current_hex.lower(), new_hex.lower()]).encode("ascii")


def verify_rotation(current_hex: str, payload: bytes, signature_b64url: str) -> bool:
    try:
        sig = base64.urlsafe_b64decode(signature_b64url + "=" * (-len(signature_b64url) % 4))
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(current_hex)).verify(sig, payload)
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def verification_methods(did: str, current_hex: str | None, history: list) -> tuple[list, str | None]:
    """The verificationMethod list and the id of the key in force.

    `history` holds the retired keys, each a mapping with key_index,
    public_key_hex and revoked_at, in any order. The registration key is
    #key-1; each rotation retires the key in force and the next key takes the
    next index, so an id never changes meaning.
    """
    retired = sorted(history, key=lambda h: h["key_index"])
    methods = []
    for h in retired:
        revoked = h["revoked_at"]
        methods.append({
            "id": f"{did}#key-{h['key_index']}",
            "type": "Ed25519VerificationKey2020",
            "controller": did,
            "publicKeyHex": h["public_key_hex"],
            "revoked": True,
            "revokedDate": revoked.isoformat() if hasattr(revoked, "isoformat") else str(revoked),
        })
    if not current_hex:
        return methods, None
    current_id = f"{did}#key-{len(retired) + 1}"
    methods.insert(0, {
        "id": current_id,
        "type": "Ed25519VerificationKey2020",
        "controller": did,
        "publicKeyHex": current_hex,
    })
    return methods, current_id
