"""Proof that the caller controls the key of an X.509-SVID (BIND-CHECK 4).

A SPIFFE binding maps a SPIFFE ID to a did:moltrust. It is accepted only
together with a signature, made with the SVID's private key, over a message
that names the SPIFFE ID, the DID and a nonce this service issued shortly
before. The SVID certificate travels with the request; its URI SAN must be the
SPIFFE ID being bound, and it must be inside its validity window.

What this proves: the caller holds the private key of a certificate that
carries this SPIFFE ID, at the time of binding. What it does not prove: that
the certificate chains to the trust domain's bundle. MolTrust holds no trust
bundles, so the chain is not checked; the binding record stores the
certificate fingerprint so a relying party that does hold the bundle can check
it later.

The nonce is an HMAC over (SPIFFE ID, DID, time) with NONCE_SECRET, valid for
NONCE_MAX_AGE seconds, so nothing is stored between challenge and bind.
"""
from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import hmac
import time

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

NONCE_MAX_AGE = 300
MAX_PEM_BYTES = 16 * 1024


class SpiffeProofError(ValueError):
    """The proof does not hold. The message names the reason."""


def binding_message(spiffe_uri: str, did: str, nonce: str) -> bytes:
    return (f"MolTrust SPIFFE Binding\nSPIFFE-ID: {spiffe_uri}\nDID: {did}\n"
            f"Nonce: {nonce}").encode("utf-8")


def _mac(secret: str, spiffe_uri: str, did: str, ts: int) -> str:
    payload = f"spiffe-bind\n{spiffe_uri}\n{did}\n{ts}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()[:32]


def issue_nonce(secret: str, spiffe_uri: str, did: str, now: float | None = None) -> str:
    ts = int(time.time() if now is None else now)
    return f"{ts}.{_mac(secret, spiffe_uri, did, ts)}"


def check_nonce(secret: str, spiffe_uri: str, did: str, nonce: str,
                now: float | None = None) -> None:
    now = time.time() if now is None else now
    try:
        ts_s, mac = nonce.split(".", 1)
        ts = int(ts_s)
    except (AttributeError, ValueError):
        raise SpiffeProofError("nonce is malformed; request one from POST /identity/spiffe/challenge")
    if ts > now + 30 or now - ts > NONCE_MAX_AGE:
        raise SpiffeProofError(f"nonce is expired (valid {NONCE_MAX_AGE} seconds)")
    if not hmac.compare_digest(mac, _mac(secret, spiffe_uri, did, ts)):
        raise SpiffeProofError("nonce was not issued for this SPIFFE ID and DID")


def _uri_sans(cert: x509.Certificate) -> list[str]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    return ext.value.get_values_for_type(x509.UniformResourceIdentifier)


def verify_svid_proof(*, spiffe_uri: str, did: str, nonce: str, svid_pem: str,
                      signature_b64: str, secret: str,
                      now: float | None = None) -> dict:
    """Check nonce, certificate and signature. Returns facts for the binding record.

    Raises SpiffeProofError with the reason on any failure.
    """
    check_nonce(secret, spiffe_uri, did, nonce, now)
    if not isinstance(svid_pem, str) or not svid_pem.strip():
        raise SpiffeProofError("svid (PEM X.509-SVID certificate) is required")
    if len(svid_pem.encode("utf-8")) > MAX_PEM_BYTES:
        raise SpiffeProofError("svid is too large")
    try:
        cert = x509.load_pem_x509_certificate(svid_pem.encode("utf-8"))
    except ValueError:
        raise SpiffeProofError("svid is not a PEM X.509 certificate")
    sans = _uri_sans(cert)
    # SPIFFE X.509-SVID: exactly one URI SAN, and it is the SPIFFE ID.
    if len(sans) != 1:
        raise SpiffeProofError("svid must carry exactly one URI SAN")
    if sans[0] != spiffe_uri:
        raise SpiffeProofError("svid URI SAN does not match spiffe_uri")
    at = datetime.datetime.fromtimestamp(time.time() if now is None else now, datetime.UTC)
    if at < cert.not_valid_before_utc or at > cert.not_valid_after_utc:
        raise SpiffeProofError("svid is outside its validity window")
    try:
        sig = base64.b64decode(signature_b64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise SpiffeProofError("signature must be base64")
    if not sig:
        raise SpiffeProofError("signature is required")
    msg = binding_message(spiffe_uri, did, nonce)
    key = cert.public_key()
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(sig, msg, ec.ECDSA(hashes.SHA256()))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(sig, msg, padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(sig, msg)
        else:
            raise SpiffeProofError("svid key type is not supported (EC, RSA or Ed25519)")
    except InvalidSignature:
        raise SpiffeProofError("signature does not verify against the svid key")
    from cryptography.hazmat.primitives.serialization import Encoding
    return {
        "svid_sha256": hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest(),
        "svid_not_after": cert.not_valid_after_utc.isoformat(),
    }
