"""AAE §5 Step 4 — subject-binding challenge-response.

Step 1 proves who signed the envelope. Step 4 proves that the party presenting
it controls the key of `credentialSubject.id`. The relying party issues a
challenge — a fresh unpredictable nonce of at least 128 bits, an audience
identifier and the AAE id — and the agent returns a compact JWS whose payload
carries exactly four members (`nonce`, `aud`, `iat`, `aae_id`), signed with
EdDSA under a verification method authorized for the subject's authentication
relation.

Six conditions, each fail-closed:

  (a) the signature verifies under a key of `credentialSubject.id`
  (b) that verification method is authorized for `authentication`
  (c) the nonce was issued by this relying party and has not been used before
  (d) `aud` identifies this relying party
  (e) `aae_id` equals the VC id
  (f) `iat` lies inside the accepted clock skew

The nonce carries its own origin proof: an HMAC over the random part, the
expiry, the audience and the AAE id. Issuing a challenge therefore needs no
database round trip, and a nonce minted for one audience or one AAE does not
verify for another. Single use is the one property an HMAC cannot express, so
it is a database invariant — `aae_subject_nonces.nonce_hash` is the primary
key, and a concurrent replay loses the insert.

did:web subjects resolve over the network and stay deferred with the signing-DID
path until the egress proxy exists; see docs/specs/d1-acceptance-gate-design.md.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt  # PyJWT
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.enforcement.jws_common import (
    ALLOWED_ALGS,
    JwsGuardError,
    check_size_caps,
    reject_duplicate_keys,
    split_kid,
)

# The relying party this deployment speaks as. Step 4 condition (d) compares the
# challenge `aud` against it, so a response minted for another verifier fails here.
RELYING_PARTY_AUD = os.environ.get("AAE_RELYING_PARTY_AUD", "did:web:api.moltrust.ch")

NONCE_TTL_SECONDS = 300
NONCE_RANDOM_BYTES = 16  # 128 bits, the floor the draft states for the nonce
# Mirrors evaluator.CLOCK_SKEW (30s); kept as a plain int here so this module has
# no import edge into the evaluator. tests/test_subject_binding.py asserts they agree.
CLOCK_SKEW_SECONDS = 30

CHALLENGE_MEMBERS = frozenset({"nonce", "aud", "iat", "aae_id"})

_DID_MOLTRUST_RE = re.compile(r"^did:moltrust:(?:ext_)?[a-f0-9]{16}$")
_NONCE_RE = re.compile(r"^[a-f0-9]{32}\.[0-9]{1,12}\.[A-Za-z0-9_-]{43}$")


class SubjectBindingError(ValueError):
    """Step 4 rejected (fail-closed). The acceptance gate maps this to its own error."""


def _secret() -> bytes:
    """HMAC key for challenge-nonce integrity.

    Uses AAE_CHALLENGE_SECRET when set, otherwise derives a stable key from the
    registry signing key, which is always present in production. The label keeps
    this key domain-separated from the registration proof-of-possession nonce in
    app/keyless_register.py, so a nonce from one flow never verifies in the other.
    """
    explicit = os.environ.get("AAE_CHALLENGE_SECRET")
    if explicit:
        return explicit.encode()
    reg = os.environ.get("MOLTRUST_REGISTRY_PRIVATE_KEY", "")
    return hashlib.sha256(b"aae-subject-challenge-v1|" + reg.encode()).digest()


def _tag(rand: str, exp: int, aud: str, aae_id: str) -> str:
    body = f"{rand}.{exp}.{aud}.{aae_id}".encode("utf-8")
    raw = hmac.new(_secret(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def nonce_hash(nonce: str) -> bytes:
    """Storage key for the used-nonce store. The raw nonce is never persisted."""
    return hashlib.sha256(nonce.encode("utf-8")).digest()


def issue_challenge(aae_id: str, *, aud: str | None = None, now: int | None = None) -> dict:
    """Mint a Step 4 challenge for one AAE.

    Returns the members the agent has to sign back, plus the expiry so a caller
    can schedule a retry. The nonce binds to `aud` and `aae_id` through its HMAC.
    """
    if not isinstance(aae_id, str) or not aae_id:
        raise SubjectBindingError("aae_id is required to issue a challenge")
    aud = aud or RELYING_PARTY_AUD
    now = int(time.time()) if now is None else now
    exp = now + NONCE_TTL_SECONDS
    rand = secrets.token_hex(NONCE_RANDOM_BYTES)
    nonce = f"{rand}.{exp}.{_tag(rand, exp, aud, aae_id)}"
    return {"nonce": nonce, "aud": aud, "aae_id": aae_id, "expires_at": exp}


def _check_nonce_origin(nonce: str, aud: str, aae_id: str, now: int) -> int:
    """Condition (c), first half: this relying party minted the nonce, unexpired.

    Returns the nonce expiry so the caller can bound the used-nonce row.
    """
    if not isinstance(nonce, str) or not _NONCE_RE.match(nonce):
        raise SubjectBindingError("challenge nonce is malformed")
    rand, exp_s, tag = nonce.split(".")
    try:
        exp = int(exp_s)
    except ValueError:
        raise SubjectBindingError("challenge nonce has a malformed expiry")
    if not hmac.compare_digest(_tag(rand, exp, aud, aae_id), tag):
        raise SubjectBindingError("challenge nonce was not issued by this relying party")
    if now > exp:
        raise SubjectBindingError("challenge nonce expired")
    return exp


def _parse_iat(iat) -> datetime:
    """`iat` is an RFC 3339 UTC timestamp string, not a JWT NumericDate."""
    if not isinstance(iat, str):
        raise SubjectBindingError("challenge iat must be an RFC 3339 timestamp string")
    try:
        parsed = datetime.fromisoformat(iat.replace("Z", "+00:00"))
    except ValueError:
        raise SubjectBindingError("challenge iat is not a valid RFC 3339 timestamp")
    if parsed.tzinfo is None:
        raise SubjectBindingError("challenge iat must carry a UTC offset")
    return parsed.astimezone(timezone.utc)


async def _resolve_moltrust_authentication(subject_did: str, kid: str, conn) -> bytes:
    """Condition (a)+(b) for did:moltrust: the key authorized for `authentication`.

    The agents DID document exposes exactly one verification method, `{did}#key-1`,
    and lists it under both `authentication` and `assertionMethod`
    (app/main.py `_build_agent_did_document`). Requiring the kid to reference that
    method is therefore the authentication-relation check for this DID method; a
    document with separate relations would need the listing compared explicitly.
    """
    if not _DID_MOLTRUST_RE.match(subject_did):
        raise SubjectBindingError("credentialSubject.id is not a valid did:moltrust")
    if kid != f"{subject_did}#key-1":
        raise SubjectBindingError(
            "kid does not reference a verification method authorized for authentication")
    row = await conn.fetchrow("SELECT public_key_hex FROM agents WHERE did = $1", subject_did)
    if not row or not row["public_key_hex"]:
        raise SubjectBindingError("subject DID not resolvable / no registered key (did:moltrust)")
    try:
        raw = bytes.fromhex(row["public_key_hex"])
    except ValueError:
        raise SubjectBindingError("registered subject key is not valid hex")
    if len(raw) != 32:
        raise SubjectBindingError("registered subject key is not a 32-byte Ed25519 public key")
    return raw


async def verify_subject_binding(
    challenge_jws: str,
    conn,
    *,
    aae_id: str,
    subject_did: str,
    aud: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Run §5 Step 4 over a challenge-response JWS. Raises SubjectBindingError on any failure.

    `aae_id` and `subject_did` come from the already-verified VC (Step 1+2), never
    from the response, so a response cannot nominate its own subject or envelope.
    """
    aud = aud or RELYING_PARTY_AUD
    now = now or datetime.now(timezone.utc)

    try:
        check_size_caps(challenge_jws, what="challenge_jws")
    except JwsGuardError as e:
        raise SubjectBindingError(str(e))

    try:
        header = jwt.get_unverified_header(challenge_jws)
    except Exception:
        raise SubjectBindingError("malformed challenge protected header")
    if header.get("alg") != "EdDSA":
        raise SubjectBindingError("challenge alg must be EdDSA")
    # An AAE envelope must not double as a challenge response: refuse the envelope cty.
    if header.get("cty") == "aae+json":
        raise SubjectBindingError('challenge must not carry cty "aae+json"')

    try:
        signing_did, _frag = split_kid(header.get("kid"))
    except JwsGuardError as e:
        raise SubjectBindingError(str(e))
    if signing_did != subject_did:
        raise SubjectBindingError("challenge kid does not belong to credentialSubject.id")

    # --- (a)+(b) key of the subject, authorized for authentication ---
    if subject_did.startswith("did:moltrust:"):
        pub_raw = await _resolve_moltrust_authentication(subject_did, header["kid"], conn)
    elif subject_did.startswith("did:web:"):
        raise NotImplementedError(
            "did:web subject resolution is deferred with the signing-DID path (egress proxy)")
    else:
        raise SubjectBindingError("unsupported DID method for credentialSubject.id")

    try:
        payload_bytes = jwt.api_jws.PyJWS().decode(
            challenge_jws, key=Ed25519PublicKey.from_public_bytes(pub_raw),
            algorithms=ALLOWED_ALGS, options={"verify_signature": True},
        )
    except Exception:
        raise SubjectBindingError("challenge signature verification failed")

    try:
        claims = json.loads(payload_bytes.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except JwsGuardError as e:
        raise SubjectBindingError(str(e))
    except Exception:
        raise SubjectBindingError("challenge payload is not valid UTF-8 JSON")
    if not isinstance(claims, dict):
        raise SubjectBindingError("challenge payload is not a JSON object")
    if set(claims) != CHALLENGE_MEMBERS:
        raise SubjectBindingError(
            "challenge payload must carry exactly nonce, aud, iat and aae_id")

    # --- (e) aae_id, then (d) aud: both settled before the nonce HMAC uses them ---
    if claims["aae_id"] != aae_id:
        raise SubjectBindingError("challenge aae_id does not match the AAE id")
    if claims["aud"] != aud:
        raise SubjectBindingError("challenge aud does not identify this relying party")

    # --- (f) iat inside the accepted skew ---
    iat = _parse_iat(claims["iat"])
    skew = timedelta(seconds=CLOCK_SKEW_SECONDS)
    if iat > now + skew:
        raise SubjectBindingError("challenge iat lies in the future beyond the accepted skew")
    if iat < now - timedelta(seconds=NONCE_TTL_SECONDS) - skew:
        raise SubjectBindingError("challenge iat is older than the challenge lifetime")

    # --- (c) nonce origin, then single use ---
    nonce = claims["nonce"]
    exp = _check_nonce_origin(nonce, aud, aae_id, int(now.timestamp()))
    claimed = await conn.fetchval(
        """
        INSERT INTO aae_subject_nonces (nonce_hash, aae_id, aud, subject_did, expires_at)
        VALUES ($1, $2, $3, $4, to_timestamp($5))
        ON CONFLICT (nonce_hash) DO NOTHING
        RETURNING nonce_hash
        """,
        nonce_hash(nonce), aae_id, aud, subject_did, exp,
    )
    if claimed is None:
        raise SubjectBindingError("challenge nonce has already been used")

    return {"subject_did": subject_did, "aae_id": aae_id, "aud": aud,
            "nonce_expires_at": exp, "iat": iat}


async def purge_expired_nonces(conn) -> int:
    """Drop used-nonce rows past their expiry. Safe to call from a scheduled job."""
    result = await conn.execute("DELETE FROM aae_subject_nonces WHERE expires_at < now()")
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0
