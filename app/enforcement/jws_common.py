"""Shared JWS parsing guards for the AAE verification path.

The acceptance gate (§5 Step 1+2) and the subject-binding check (§5 Step 4)
both parse an externally supplied compact JWS. The hardening from the D-1
security review applies to both, so it lives here once rather than in two
copies: an explicit algorithm allowlist, strict kid validation before any
resolution, a duplicate-key-rejecting JSON hook, and size caps that bound the
input before base64-decode, parse or verify.
"""
from __future__ import annotations

ALLOWED_ALGS = ["EdDSA"]

# DoS caps: bound the input BEFORE base64-decode / JSON-parse / Ed25519-verify.
MAX_JWS_BYTES = 16 * 1024          # whole compact-JWS string
MAX_PAYLOAD_B64URL = 11000         # ~8KB decoded payload (b64url ~ 4/3)


class JwsGuardError(ValueError):
    """A JWS failed a structural guard. Callers map this to their own error type."""


def split_kid(kid) -> tuple[str, str]:
    """kid = DID URL 'did:...:<id>#<fragment>'. Returns (signing_did, fragment), strict."""
    if not isinstance(kid, str) or "#" not in kid:
        raise JwsGuardError("kid must be a DID URL with a verification-method fragment")
    if not kid.isascii():
        raise JwsGuardError("kid must be ASCII (look-alike / homoglyph protection)")
    if ".." in kid or "/" in kid or "\\" in kid:
        raise JwsGuardError("illegal characters in kid (path-traversal protection)")
    did_part, _, frag = kid.partition("#")
    if not did_part or not frag:
        raise JwsGuardError("kid must have a non-empty DID and fragment")
    return did_part, frag


def reject_duplicate_keys(pairs):
    """object_pairs_hook that refuses duplicate JSON keys (no last-wins confusion)."""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise JwsGuardError(f"duplicate JSON key in payload: {k}")
        seen[k] = v
    return seen


def check_size_caps(jws: str, *, what: str = "jws") -> None:
    """Reject an oversized compact JWS before any decode or key operation."""
    if not isinstance(jws, str) or jws.count(".") != 2:
        raise JwsGuardError(f"{what} must be a compact JWS (header.payload.signature)")
    if len(jws.encode("utf-8")) > MAX_JWS_BYTES:
        raise JwsGuardError(f"{what} exceeds size limit")
    if len(jws.split(".", 2)[1]) > MAX_PAYLOAD_B64URL:
        raise JwsGuardError(f"{what} payload exceeds size limit")
