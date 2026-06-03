"""AAE Acceptance-Gate — D-1, Phase A (did:moltrust-only).

Verifies the compact JWS an issuer signed (AAE draft-04 §5 Step 1 + Step 2),
fail-closed, at submit-time. Phase A resolves only did:moltrust (registry / agents
table, no outbound); did:web is Phase B (needs the egress-proxy).

Hardening (per #128 Review-Härtung):
- alg-confusion: JWS verified with an EXPLICIT algorithms=["EdDSA"] allowlist;
  header alg is never trusted (alg=none / HS* / RS* -> reject).
- kid: strict DID-URL validation BEFORE resolution (path-traversal / look-alike).
- canonicalization: raw_canonical = the EXACT base64url-decoded payload bytes
  (what was signed); never re-serialized. Parsed JSON is for schema only.
- JSON duplicate-keys rejected via object_pairs_hook.
"""
from __future__ import annotations

import json
import re

import jwt  # PyJWT
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ALLOWED_ALGS = ["EdDSA"]
CTY_AAE = "aae+json"
# signing-DID strict format (matches main.DID_PATTERN); the fragment is checked separately.
_DID_MOLTRUST_RE = re.compile(r"^did:moltrust:(?:ext_)?[a-f0-9]{16}$")


class AcceptanceError(ValueError):
    """AAE acceptance rejected (fail-closed). Maps to HTTP 422 at the boundary."""


def _split_kid(kid) -> tuple[str, str]:
    """kid = DID-URL 'did:...:<id>#<fragment>'. Returns (signing_did, fragment), strict."""
    if not isinstance(kid, str) or "#" not in kid:
        raise AcceptanceError("kid must be a DID URL with a verification-method fragment")
    if not kid.isascii():
        raise AcceptanceError("kid must be ASCII (look-alike / homoglyph protection)")
    if ".." in kid or "/" in kid or "\\" in kid:
        raise AcceptanceError("illegal characters in kid (path-traversal protection)")
    did_part, _, frag = kid.partition("#")
    if not did_part or not frag:
        raise AcceptanceError("kid must have a non-empty DID and fragment")
    return did_part, frag


def _reject_duplicate_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise AcceptanceError(f"duplicate JSON key in payload: {k}")
        seen[k] = v
    return seen


async def _resolve_moltrust_ed25519(signing_did: str, kid: str, conn) -> bytes:
    """did:moltrust resolution. Returns the 32-byte Ed25519 public key authorized
    for assertionMethod, or raises. Reflects the agents-table DID-doc shape
    (single key `{did}#key-1`, listed in assertionMethod)."""
    if not _DID_MOLTRUST_RE.match(signing_did):
        raise AcceptanceError("signing DID is not a valid did:moltrust")
    # The agents DID-doc exposes exactly one verification method, {did}#key-1, which is
    # authorized for assertionMethod. Require the kid to reference it.
    if kid != f"{signing_did}#key-1":
        raise AcceptanceError("kid does not reference the assertionMethod verification method")
    row = await conn.fetchrow("SELECT public_key_hex FROM agents WHERE did = $1", signing_did)
    if not row or not row["public_key_hex"]:
        raise AcceptanceError("signing DID not resolvable / no registered key (did:moltrust)")
    try:
        raw = bytes.fromhex(row["public_key_hex"])
    except ValueError:
        raise AcceptanceError("registered public key is not valid hex")
    if len(raw) != 32:
        raise AcceptanceError("registered key is not a 32-byte Ed25519 public key")
    return raw


async def verify_aae_jws(aae_jws: str, conn) -> dict:
    """§5 Step 1 (signature + signing-authority) + Step 2 (payload/schema/cty).

    Returns the verified envelope fields (incl. raw_canonical = exact signed bytes,
    issuer_trust_tier) on success; raises AcceptanceError (fail-closed) on any failure.
    """
    if not isinstance(aae_jws, str) or aae_jws.count(".") != 2:
        raise AcceptanceError("aae_jws must be a compact JWS (header.payload.signature)")

    # --- read protected header WITHOUT trusting it (to obtain alg / cty / kid) ---
    try:
        header = jwt.get_unverified_header(aae_jws)
    except Exception:
        raise AcceptanceError("malformed JWS protected header")

    # alg-confusion guard: explicit, before any key operation. Never trust header alg as policy.
    if header.get("alg") != "EdDSA":
        raise AcceptanceError("alg must be EdDSA")
    if header.get("cty") != CTY_AAE:
        raise AcceptanceError('protected-header cty must be "aae+json"')

    signing_did, _frag = _split_kid(header.get("kid"))

    # --- DID-method dispatch (Phase A: did:moltrust only) ---
    if signing_did.startswith("did:moltrust:"):
        pub_raw = await _resolve_moltrust_ed25519(signing_did, header["kid"], conn)
        issuer_trust_tier = "trusted"  # did:moltrust registry key
    elif signing_did.startswith("did:web:"):
        raise NotImplementedError("did:web resolution is Phase B (requires egress-proxy)")
    else:
        raise AcceptanceError("unsupported DID method (Phase A: did:moltrust only)")

    pub = Ed25519PublicKey.from_public_bytes(pub_raw)

    # --- verify signature with EXPLICIT allowlist -> exact decoded payload bytes ---
    try:
        payload_bytes = jwt.api_jws.PyJWS().decode(aae_jws, key=pub, algorithms=ALLOWED_ALGS)
    except Exception:
        raise AcceptanceError("JWS signature verification failed")
    # payload_bytes are the exact bytes the issuer signed — NEVER re-serialize these.

    # --- Step 2: payload / schema (parse for checks only; signature already bound to bytes) ---
    try:
        vc = json.loads(payload_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except AcceptanceError:
        raise
    except Exception:
        raise AcceptanceError("payload is not valid UTF-8 JSON")
    if not isinstance(vc, dict):
        raise AcceptanceError("payload is not a JSON object")

    issuer = vc.get("issuer")
    cs = vc.get("credentialSubject")
    if not isinstance(vc.get("id"), str) or not isinstance(issuer, str) or not isinstance(cs, dict):
        raise AcceptanceError("VC must contain id, issuer (string), credentialSubject (object)")
    if not isinstance(cs.get("id"), str):
        raise AcceptanceError("VC missing credentialSubject.id")
    aae = cs.get("aae")
    if not isinstance(aae, dict) or not all(k in aae for k in ("mandate", "constraints", "validity")):
        raise AcceptanceError("credentialSubject.aae must contain mandate, constraints, validity")

    # --- signing-authority (non-delegated): signing-DID MUST equal VC issuer ---
    if signing_did != issuer:
        raise AcceptanceError("signing DID does not match VC issuer")

    return {
        "raw_canonical": payload_bytes,      # exact signed bytes (-> aae_ref = sha256(raw_canonical))
        "aae_id": vc["id"],
        "issuer_did": issuer,
        "envelope_signature": aae_jws,
        "mandate": aae["mandate"],
        "constraints": aae["constraints"],
        "validity": aae["validity"],
        "aae_version": str(aae.get("aae_version", "1.0")),
        "taxonomy_version": str(aae.get("taxonomy_version", "1.0")),
        "issuer_trust_tier": issuer_trust_tier,
    }
