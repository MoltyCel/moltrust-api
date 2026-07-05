"""
ML-DSA-65 (Dilithium3) post-quantum signing for MolTrust.

Uses liboqs-python (Open Quantum Safe). Install with:
    pip install liboqs-python

Key storage mirrors the Ed25519 pattern in app/crypto/kms_signer.py:
  - Primary: AWS KMS-encrypted blob  (env: DILITHIUM_PRIVATE_KEY_ENCRYPTED)
  - Fallback (dev only): hex env vars (DILITHIUM_PRIVATE_KEY_HEX +
    DILITHIUM_PUBLIC_KEY_HEX). Disabled when MOLTRUST_ENV=production, the same
    gate the Ed25519 signer uses.
  - Public key: env DILITHIUM_PUBLIC_KEY_HEX (always required, never secret).

If no Dilithium key is configured AND liboqs-python is not importable, PQC
signing is gracefully skipped and credentials are issued Ed25519-only. This
is the Phase 1 -> Phase 2 transition path described in the PR.

The cached keypair is held in memory only for CACHE_TTL seconds, mirroring
the Ed25519 KMS cache. It is never written to disk or logs.
"""
import os
import logging
import base64
import time

logger = logging.getLogger("moltrust.crypto.dilithium")

ALGORITHM = "ML-DSA-65"

_cached_keypair = None          # (secret_key_bytes, public_key_bytes) or None
_cache_expiry = 0
_CACHE_TTL = 300                # 5 minutes, matches kms_signer.CACHE_TTL


def _is_production() -> bool:
    return os.environ.get("MOLTRUST_ENV", "").lower() == "production"


def _load_keypair() -> tuple[bytes, bytes] | None:
    """Load the Dilithium keypair. Returns (secret_key, public_key) or None.

    None means "PQC not configured" -> caller falls back to Ed25519-only.
    A configured-but-broken state (e.g. KMS failure) is logged and also
    returns None so issuance degrades safely rather than crashing.
    """
    global _cached_keypair, _cache_expiry

    now = time.time()
    if _cached_keypair is not None and now < _cache_expiry:
        return _cached_keypair

    pk_hex = os.environ.get("DILITHIUM_PUBLIC_KEY_HEX", "").strip()
    if not pk_hex:
        # No public key configured -> PQC is simply off.
        return None

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
            if not sk_hex:
                logger.error("KMS decrypted to an empty Dilithium secret key")
                return None
            _cached_keypair = (bytes.fromhex(sk_hex), bytes.fromhex(pk_hex))
            _cache_expiry = now + _CACHE_TTL
            return _cached_keypair
        except Exception as e:
            logger.error("Dilithium KMS decryption failed: %s", e)
            return None

    # Plaintext hex fallback — dev/local only, gated off in production.
    if _is_production():
        logger.error(
            "Plaintext DILITHIUM_PRIVATE_KEY_HEX fallback disabled in production"
        )
        return None

    sk_hex = os.environ.get("DILITHIUM_PRIVATE_KEY_HEX", "").strip()
    if not sk_hex:
        logger.error("DILITHIUM_PUBLIC_KEY_HEX set but no private key configured")
        return None

    try:
        _cached_keypair = (bytes.fromhex(sk_hex), bytes.fromhex(pk_hex))
    except ValueError as e:
        logger.error("Dilithium key hex parsing failed: %s", e)
        return None
    _cache_expiry = now + _CACHE_TTL
    return _cached_keypair


def is_available() -> bool:
    """True iff liboqs-python is importable AND a keypair is configured."""
    try:
        import oqs  # noqa: F401
    except ImportError:
        return False
    return _load_keypair() is not None


def public_key_configured() -> bool:
    """True iff the Dilithium public key is configured (env var set).

    Used by the verify policy to determine whether the issuer is PQC-capable.
    Does NOT require the private key (which is only needed for signing) or
    even a working liboqs install — the policy fires whenever the issuer has
    declared a PQC key, independent of KMS availability. Verification of the
    Dilithium leg itself still depends on liboqs being importable.
    """
    return bool(os.environ.get("DILITHIUM_PUBLIC_KEY_HEX", "").strip())


def sign(payload: bytes) -> bytes | None:
    """Sign payload with ML-DSA-65. Returns signature bytes or None.

    None means PQC is not available; the caller MUST then keep the credential
    Ed25519-only (never emit a Dilithium proof that has no real signature).
    """
    keypair = _load_keypair()
    if not keypair:
        return None
    try:
        import oqs
        sk, _pk = keypair
        signer = oqs.Signature(ALGORITHM, secret_key=sk)
        return signer.sign(payload)
    except Exception as e:
        logger.error("Dilithium signing failed: %s", e)
        return None


def verify(payload: bytes, signature: bytes, public_key: bytes) -> bool:
    """Verify an ML-DSA-65 signature. Returns True/False, never raises."""
    try:
        import oqs
        verifier = oqs.Signature(ALGORITHM)
        return verifier.verify(payload, signature, public_key)
    except Exception as e:
        logger.error("Dilithium verification failed: %s", e)
        return False


def get_public_key_hex() -> str | None:
    """Return the Dilithium public key as hex, or None if not configured."""
    keypair = _load_keypair()
    if not keypair:
        return None
    return keypair[1].hex()


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh ML-DSA-65 keypair. Returns (secret_key, public_key).

    Utility for one-time key generation. The caller is responsible for
    writing the secret key to a secure location (chmod 600 file or KMS);
    it must NEVER be printed to stdout/stderr/logs (security review blocker).
    """
    import oqs
    signer = oqs.Signature(ALGORITHM)
    pk = signer.generate_keypair()
    sk = signer.export_secret_key()
    return sk, pk


def clear_cache() -> None:
    """Drop the cached keypair (used during key rotation)."""
    global _cached_keypair, _cache_expiry
    _cached_keypair = None
    _cache_expiry = 0