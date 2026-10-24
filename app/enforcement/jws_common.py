"""Shared JWS parsing guards for the AAE verification path.

The acceptance gate (§5 Step 1+2) and the subject-binding check (§5 Step 4)
both parse an externally supplied compact JWS. The hardening from the D-1
security review applies to both, so it lives here once rather than in two
copies: an explicit algorithm allowlist, strict kid validation before any
resolution, a duplicate-key-rejecting JSON hook, and size caps that bound the
input before base64-decode, parse or verify.

The duplicate-key rule covers the protected header as well as the payload.
`jwt.get_unverified_header()` resolves duplicate members last-wins, so a header
carrying two `kid` values parses without complaint and a reader and a verifier
can disagree about which one applies. `protected_header()` below is the
replacement; nothing in this path should read a header through a parser whose
answer depends on which duplicate it kept.
"""
from __future__ import annotations

import base64
import json
import re

ALLOWED_ALGS = ["EdDSA"]

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")

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
    """object_pairs_hook that refuses duplicate JSON keys (no last-wins confusion).

    Used for both the protected header and the payload, so the message names neither.
    """
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise JwsGuardError(f"duplicate JSON member: {k}")
        seen[k] = v
    return seen


def protected_header(jws: str, *, what: str = "jws") -> dict:
    """The JOSE protected header, parsed with the same duplicate-key rule as the payload.

    Call this instead of `jwt.get_unverified_header()`. Same contract otherwise: the
    header is read WITHOUT being trusted, so that alg, cty and kid can be inspected
    before any key is fetched. What changes is that a duplicate member is refused
    rather than silently resolved -- without that, "the kid" is a statement about the
    parser and not about the token.

    Raises JwsGuardError on anything that is not a base64url-encoded JSON object.
    Call `check_size_caps()` first; this function does not bound its input.
    """
    if not isinstance(jws, str) or jws.count(".") != 2:
        raise JwsGuardError(f"{what} must be a compact JWS (header.payload.signature)")
    segment = jws.split(".", 1)[0]
    if not _B64URL_RE.match(segment):
        raise JwsGuardError(f"malformed {what} protected header (not base64url)")
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except Exception:
        raise JwsGuardError(f"malformed {what} protected header (not base64url)")
    try:
        header = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except JwsGuardError:
        raise                                   # duplicate member: keep the precise reason
    except Exception:
        raise JwsGuardError(f"malformed {what} protected header (not UTF-8 JSON)")
    if not isinstance(header, dict):
        raise JwsGuardError(f"{what} protected header is not a JSON object")
    return header


def check_size_caps(jws: str, *, what: str = "jws") -> None:
    """Reject an oversized compact JWS before any decode or key operation."""
    if not isinstance(jws, str) or jws.count(".") != 2:
        raise JwsGuardError(f"{what} must be a compact JWS (header.payload.signature)")
    if len(jws.encode("utf-8")) > MAX_JWS_BYTES:
        raise JwsGuardError(f"{what} exceeds size limit")
    if len(jws.split(".", 2)[1]) > MAX_PAYLOAD_B64URL:
        raise JwsGuardError(f"{what} payload exceeds size limit")
