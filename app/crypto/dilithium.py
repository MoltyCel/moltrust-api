"""
ML-DSA-65 (Dilithium3) post-quantum signing for MolTrust.

Uses liboqs (Open Quantum Safe) — MIT licensed.
Install: pip install liboqs-python

Key storage follows the same pattern as Ed25519:
- Primary: encrypted via AWS KMS (env: DILITHIUM_PRIVATE_KEY_ENCRYPTED)
- Fallback: hex env var (env: DILITHIUM_PRIVATE_KEY_HEX) — dev only
- Public key: env: DILITHIUM_PUBLIC_KEY_HEX

If no Dilithium key is configured, PQC signing is gracefully skipped
and credentials are issued with Ed25519 only (Phase 1 → Phase 2 transition).
"""
import os
import logging
import base64
import time

logger = logging.getLogger("moltrust.crypto.dilithium")

ALGORITHM = "ML-DSA-65"

_cached_keypair = None
_cache_expiry = 0
_CACHE_TTL = 300  # 5 minutes


def _load_keypair() -> tuple[bytes, bytes] | None:
    """Load Dilithium keypair. Returns (secret_key, public_key) or None."""
    global _cached_keypair, _cache_expiry

    now = time.time()
    if _cached_keypair and now < _cache_expiry:
        return _cached_keypair

    # Try KMS-encrypted key first
    encrypted = os.environ.get("DILITHIUM_PRIVATE_KEY_ENCRYPTED")
    if encrypted:
        try:
            import boto3
            kms = boto3.client("kms", region_name=os.environ.get("AWS_REGION", "eu-central-1"))
            response = kms.decrypt(
                KeyId=os.environ.get("KMS_KEY_ID"),
                CiphertextBlob=base64.b64decode(encrypted),
            )
            sk_hex = response["Plaintext"].decode("utf-8").strip()
            pk_hex = os.environ.get("DILITHIUM_PUBLIC_KEY_HEX", "")
            if not pk_hex:
                logger.error("DILITHIUM_PUBLIC_KEY_HEX required when using KMS")
                return None
            _cached_keypair = (bytes.fromhex(sk_hex), bytes.fromhex(pk_hex))
            _cache_expiry = now + _CACHE_TTL
            return _cached_keypair
        except Exception as e:
            logger.error(f"Dilithium KMS decryption failed: {e}")
            return None

    # Fallback: plaintext hex env vars (development only)
    sk_hex = os.environ.get("DILITHIUM_PRIVATE_KEY_HEX", "")
    pk_hex = os.environ.get("DILITHIUM_PUBLIC_KEY_HEX", "")
    if sk_hex and pk_hex:
        _cached_keypair = (bytes.fromhex(sk_hex), bytes.fromhex(pk_hex))
        _cache_expiry = now + _CACHE_TTL
        return _cached_keypair

    return None


def is_available() -> bool:
    """Check if Dilithium signing is configured and liboqs is installed."""
    try:
        import oqs  # noqa: F401
    except ImportError:
        return False
    return _load_keypair() is not None


def sign(payload: bytes) -> bytes | None:
    """Sign payload with Dilithium3. Returns signature or None if not configured."""
    keypair = _load_keypair()
    if not keypair:
        return None
    try:
        import oqs
        sk, _ = keypair
        signer = oqs.Signature(ALGORITHM, secret_key=sk)
        return signer.sign(payload)
    except Exception as e:
        logger.error(f"Dilithium signing failed: {e}")
        return None


def verify(payload: bytes, signature: bytes, public_key: bytes) -> bool:
    """Verify a Dilithium3 signature."""
    try:
        import oqs
        verifier = oqs.Signature(ALGORITHM)
        return verifier.verify(payload, signature, public_key)
    except Exception as e:
        logger.error(f"Dilithium verification failed: {e}")
        return False


def get_public_key_hex() -> str | None:
    """Return the Dilithium public key as hex, or None if not configured."""
    keypair = _load_keypair()
    if not keypair:
        return None
    return keypair[1].hex()


def generate_keypair() -> tuple[str, str]:
    """Generate a new Dilithium3 keypair. Returns (secret_key_hex, public_key_hex).

    Utility for initial key generation — run once, store the keys securely.
    """
    import oqs
    signer = oqs.Signature(ALGORITHM)
    pk = signer.generate_keypair()
    sk = signer.export_secret_key()
    return sk.hex(), pk.hex()


def clear_cache():
    """Clear cached keypair (for rotation)."""
    global _cached_keypair, _cache_expiry
    _cached_keypair = None
    _cache_expiry = 0
