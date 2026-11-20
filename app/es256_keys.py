"""Second registry signing key: ECDSA P-256 (JWS alg ES256), next to Ed25519.

KEY-AGILITY, 20.11.2026. Ed25519 stays the default for everything that signs
today. This key exists for interfaces that refuse Ed25519 — AWS Marketplace
x402 quotes accept only EC keys, OpenID4VC HAIP and ANS require ES256.

Off unless both hold:
  KEY_AGILITY_ES256_ENABLED=true
  MOLTRUST_ES256_PRIVATE_KEY=<64 hex chars, the P-256 private scalar>

Stored like MOLTRUST_REGISTRY_PRIVATE_KEY: one hex line in ~/.moltrust_secrets,
never in the repo. With the flag on and the key missing or malformed, every
accessor raises Es256Unavailable; callers answer 503, never fall back to a
different key (CLAUDE.md, "Kein Rückfallwert in der erlaubenden Richtung").

kid: moltrust-es256-2026-v1. Rotation: config/key_purposes.json, purpose
"registry-es256".
"""
from __future__ import annotations

import base64
import json
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

ES256_KID = "moltrust-es256-2026-v1"
FLAG = "KEY_AGILITY_ES256_ENABLED"
KEY_VAR = "MOLTRUST_ES256_PRIVATE_KEY"


class Es256Unavailable(RuntimeError):
    """The flag is off, or it is on and the key is missing or unusable."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() == "true"


def get_private_key() -> ec.EllipticCurvePrivateKey:
    if not enabled():
        raise Es256Unavailable(f"{FLAG} is not true")
    hex_key = os.environ.get("MOLTRUST_ES256_PRIVATE_KEY", "").strip()
    if not hex_key:
        raise Es256Unavailable(f"{KEY_VAR} not set")
    try:
        raw = bytes.fromhex(hex_key)
    except ValueError as exc:
        raise Es256Unavailable(f"{KEY_VAR} is not hex") from exc
    if len(raw) != 32:
        raise Es256Unavailable(f"expected a 32-byte P-256 scalar, got {len(raw)} bytes")
    try:
        return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    except ValueError as exc:
        raise Es256Unavailable(f"{KEY_VAR} is not a valid P-256 scalar") from exc


def _xy(public_key: ec.EllipticCurvePublicKey) -> tuple[bytes, bytes]:
    nums = public_key.public_numbers()
    return nums.x.to_bytes(32, "big"), nums.y.to_bytes(32, "big")


def get_public_jwk() -> dict:
    x, y = _xy(get_private_key().public_key())
    return {
        "kid": ES256_KID,
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url(x),
        "y": _b64url(y),
        "use": "sig",
        "alg": "ES256",
    }


def get_public_multikey() -> str:
    """publicKeyMultibase for a Multikey verification method: base58btc of the
    p256-pub multicodec (0x1200, varint 0x80 0x24) plus the compressed point."""
    x, y = _xy(get_private_key().public_key())
    compressed = bytes([0x02 | (y[-1] & 1)]) + x
    return "z" + _b58encode(bytes([0x80, 0x24]) + compressed)


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def sign_jws(payload: dict, typ: str = "JWT", extra_header: dict | None = None) -> str:
    """Compact JWS over the JSON payload, alg ES256, signature as raw r||s."""
    header = {"alg": "ES256", "typ": typ, "kid": ES256_KID}
    if extra_header:
        header.update(extra_header)
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    der = get_private_key().sign(f"{h}.{p}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{h}.{p}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def verify_jws(token: str, jwk: dict) -> dict:
    """Verify an ES256 compact JWS against one EC P-256 JWK. Returns the payload.

    Raises ValueError for any failure. The key fixes the algorithm: a header
    that names anything but ES256 is refused before the signature is looked at.
    """
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("not an EC P-256 key")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a compact JWS")
    header = json.loads(_b64url_decode(parts[0]))
    if header.get("alg") != "ES256":
        raise ValueError(f"alg={header.get('alg')!r}, key is ES256")
    if header.get("kid") != jwk.get("kid"):
        raise ValueError("kid does not match the key")
    sig = _b64url_decode(parts[2])
    if len(sig) != 64:
        raise ValueError("ES256 signature must be 64 bytes r||s")
    x, y = _b64url_decode(jwk["x"]), _b64url_decode(jwk["y"])
    key = ec.EllipticCurvePublicNumbers(
        int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
    ).public_key()
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    try:
        key.verify(der, f"{parts[0]}.{parts[1]}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError("signature does not cover this payload") from exc
    return json.loads(_b64url_decode(parts[1]))


def ec_only_jwks() -> dict:
    """JWKS with the EC key alone. AWS Marketplace documents EC-only keys for
    quote signing and its seller SDK refuses OKP entries, so this set never
    carries the Ed25519 keys."""
    return {"keys": [get_public_jwk()]}
