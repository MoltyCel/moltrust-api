"""Keyless agent registration via Ed25519 Proof-of-Possession (PoP).

Pure helpers — no app/db imports — so they unit-test in isolation. The HTTP
routes live in app.main and combine these with db_pool + issue_credential /
grant_credits (the same primitives /identity/register uses).

Flow:
  1. GET  /identity/register-challenge -> a stateless, HMAC-signed nonce.
  2. Agent generates an Ed25519 keypair locally, signs the exact challenge
     string, and POSTs {public_key, challenge, signature, display_name} to
  3. POST /identity/register-pop -> server checks the nonce HMAC + TTL and the
     Ed25519 signature (proof the caller holds the private key), then mints a
     DID + signed VC. No API key, no signup, and no spendable credits — see
     the note at the credits grant in main.py::register_agent_pop.

The challenge is stateless: `<rand>.<exp>.<hmac>` where hmac = HMAC-SHA256 over
`<rand>.<exp>` keyed by a server secret. No challenge store / DB round-trip.

What actually limits abuse here is the per-IP rate limit on the routes
(30/hour on register-pop), not the PoW below — see the note at
POW_DIFFICULTY_BITS. Trust starts at 0 and Sybil-resistance lives in the
endorsement graph, so there is deliberately no Sybil gate.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CHALLENGE_TTL_SECONDS = 300  # 5 minutes


def _secret() -> bytes:
    """HMAC key for challenge integrity.

    Uses POP_CHALLENGE_SECRET if set; otherwise derives a stable key from the
    registry signing key (always present in prod) so no new secret is required.
    The HMAC output never reveals the underlying key.
    """
    explicit = os.environ.get("POP_CHALLENGE_SECRET")
    if explicit:
        return explicit.encode()
    reg = os.environ.get("MOLTRUST_REGISTRY_PRIVATE_KEY", "")
    return hashlib.sha256(b"pop-challenge-v1|" + reg.encode()).digest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_challenge(now: int | None = None) -> dict:
    """Return {'challenge': '<rand>.<exp>.<hmac>', 'expires_at': <ts>}."""
    now = int(time.time()) if now is None else now
    exp = now + CHALLENGE_TTL_SECONDS
    body = f"{secrets.token_hex(16)}.{exp}"
    tag = _b64url(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return {"challenge": f"{body}.{tag}", "expires_at": exp}


def verify_challenge(challenge: str, now: int | None = None) -> tuple[bool, str]:
    """Validate a challenge's HMAC and TTL. Returns (ok, error)."""
    now = int(time.time()) if now is None else now
    parts = challenge.split(".")
    if len(parts) != 3:
        return False, "malformed challenge"
    body = f"{parts[0]}.{parts[1]}"
    expected = _b64url(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        return False, "bad challenge signature"
    try:
        exp = int(parts[1])
    except ValueError:
        return False, "bad expiry"
    if now > exp:
        return False, "challenge expired"
    return True, ""


def verify_pop(public_key_hex: str, challenge: str, signature_b64url: str) -> tuple[bool, str]:
    """Verify an Ed25519 signature over `challenge` by `public_key_hex`.

    Returns (ok, error). This is the proof-of-possession: a valid signature
    proves the caller holds the private key for the presented public key.
    """
    try:
        pk_raw = bytes.fromhex(public_key_hex.strip())
    except ValueError:
        return False, "public_key must be hex"
    if len(pk_raw) != 32:
        return False, "public_key must be a 32-byte Ed25519 key (64 hex chars)"
    try:
        sig = _b64url_decode(signature_b64url.strip())
    except Exception:
        return False, "signature must be base64url"
    try:
        Ed25519PublicKey.from_public_bytes(pk_raw).verify(sig, challenge.encode("ascii"))
    except InvalidSignature:
        return False, "signature does not verify for this challenge + public_key"
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"verify error: {type(exc).__name__}"
    return True, ""


# --- Proof-of-Work (currently decorative — read before relying on it) --------
# Difficulty calibrated on the prod host (pure-python single-thread, n=15):
#   N=18 -> median 67.8 ms (p90 260 ms), N=20 -> median 355 ms. N=18 keeps a real
# agent under the ~100 ms one-time target. PoW is probabilistic, so an individual
# solve varies (worst observed ~360 ms); optimized solvers are faster.
#
# What this PoW does NOT do, despite the section title it used to carry:
#
# The challenge carries no used-flag and the PoW seed is the challenge's random
# component, not the public key. One solved PoW therefore covers every
# registration made with that challenge until its 300 s TTL expires — sign the
# same challenge with as many fresh keypairs as you like, each yields a DID.
# Per-registration cost after the first solve is an Ed25519 keygen plus a
# signature, i.e. microseconds.
#
# An earlier version of this comment claimed "~1.9 CPU-hours per 100k" mints.
# That figure assumed one solve per registration and does not hold: 100k mints
# need roughly one solve per 300 s window, well under a second of CPU in total.
#
# The real ceiling is the per-IP rate limit on the routes — 30/hour on
# register-pop. Against that limit the PoW is already negligible: 30 solves cost
# about two seconds of CPU per hour. It would only start to matter for a caller
# spread across many IPs, and that is exactly the case the replay defeats.
#
# Keeping it is defensible while the limit is the binding constraint and the
# funnel grants no credits. Before raising the rate limit, or if multi-IP mints
# show up, it needs to be sharpened (used-flag + bind the PoW to the public key)
# or removed. Tracked in docs/BACKLOG.md.
POW_DIFFICULTY_BITS = 18


def pow_seed(challenge: str) -> str:
    """PoW seed = the challenge's random component.

    It is the first field of the HMAC-signed challenge, so it is unique per
    challenge and cannot be forged. It is NOT unique per registration: the seed
    does not include the public key, and the challenge is never marked used, so
    one solution serves every registration made with that challenge inside its
    TTL. Binding the seed to the public key is half of what sharpening this PoW
    would take — the other half is a used-flag on the challenge.
    """
    return challenge.split(".", 1)[0]


def _leading_zero_bits(digest: bytes) -> int:
    n = 0
    for byte in digest:
        if byte == 0:
            n += 8
            continue
        n += 8 - byte.bit_length()
        break
    return n


def verify_pow(seed: str, nonce: str, bits: int = POW_DIFFICULTY_BITS) -> tuple[bool, str]:
    """True iff sha256(seed || nonce) has >= `bits` leading zero bits."""
    if not isinstance(nonce, str) or not nonce or len(nonce) > 64:
        return False, "pow_nonce must be a 1-64 char string"
    digest = hashlib.sha256(f"{seed}{nonce}".encode("ascii")).digest()
    if _leading_zero_bits(digest) >= bits:
        return True, ""
    return False, f"pow: sha256(seed||nonce) needs {bits} leading zero bits"


def solve_pow(seed: str, bits: int = POW_DIFFICULTY_BITS) -> str:
    """Reference solver for clients/tests. Returns a hex nonce meeting difficulty."""
    i = 0
    while True:
        nonce = format(i, "x")
        if _leading_zero_bits(hashlib.sha256(f"{seed}{nonce}".encode("ascii")).digest()) >= bits:
            return nonce
        i += 1
