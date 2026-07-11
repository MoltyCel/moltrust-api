"""UCAN 0.10.0 delegation tokens (JWT), minting + verification.

Pinned to docs/spec-fakten/ucan-0.10.0.md. MolTrust registry Ed25519 key signs
all tokens (iss = did:web:api.moltrust.ch, verifiable via /.well-known/jwks.json);
the delegating agent DID is carried in fct.delegator. Proofs are embedded UCAN
JWT strings in `prf` (self-contained chain) — deliberate, documented deviation
from 0.10.0's [CID] proofs so a chain verifies end-to-end in one call.
"""
from __future__ import annotations

import base64
import datetime
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.registry_keys import get_private_key, get_public_key_bytes

UCAN_VERSION = "0.10.0"
ISSUER_DID = "did:web:api.moltrust.ch"
MAX_CHAIN_DEPTH = 8


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _now() -> int:
    return int(datetime.datetime.utcnow().timestamp())


# --- Minting ----------------------------------------------------------------
def mint_ucan(*, delegator_did: str, audience_did: str, capabilities: dict,
              ttl_seconds: int = 3600, not_before: int | None = None,
              nonce: str | None = None, facts: dict | None = None,
              proofs: list[str] | None = None) -> str:
    """Mint a signed UCAN 0.10.0 JWT. Signature = Ed25519 over b64(header).b64(payload)."""
    header = {"alg": "EdDSA", "typ": "JWT"}
    now = _now()
    fct = dict(facts or {})
    fct["delegator"] = delegator_did
    payload = {
        "ucv": UCAN_VERSION,
        "iss": ISSUER_DID,
        "aud": audience_did,
        "nbf": not_before if not_before is not None else now,
        "exp": now + int(ttl_seconds) if ttl_seconds else None,
        "cap": capabilities,
        "fct": fct,
        "prf": proofs or [],
    }
    if nonce:
        payload["nnc"] = nonce
    h_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    p_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    sig = get_private_key().sign(signing_input)
    return f"{h_b64}.{p_b64}.{_b64url_encode(sig)}"


# --- Attenuation ------------------------------------------------------------
def _caveat_narrower(child_cavs: list, parent_cavs: list) -> bool:
    """child caveat array must be equal-or-narrower than parent (0.10.0 rules)."""
    if parent_cavs == []:          # empty array disallows the capability
        return False
    if parent_cavs == [{}]:        # parent unrestricted -> any child narrows
        return True
    if child_cavs == [{}] or child_cavs == []:
        return False               # escalation to unrestricted / disallow mismatch
    # every child caveat must be a superset (>= restrictive) of some parent caveat
    for c in child_cavs:
        if not isinstance(c, dict):
            return False
        if not any(isinstance(p, dict) and all(c.get(k) == v for k, v in p.items()) for p in parent_cavs):
            return False
    return True


def cap_is_narrower(child_cap: dict, parent_cap: dict) -> bool:
    """Every resource+ability in child must be present in parent and equal-or-narrower."""
    if not isinstance(child_cap, dict) or not isinstance(parent_cap, dict):
        return False
    for resource, abilities in child_cap.items():
        if resource not in parent_cap:
            return False
        for ability, caveats in abilities.items():
            if ability not in parent_cap[resource]:
                return False
            if not _caveat_narrower(caveats, parent_cap[resource][ability]):
                return False
    return True


# --- Verification -----------------------------------------------------------
def _decode_token(token: str) -> tuple[dict, dict, bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a compact JWT (need 3 segments)")
    header = json.loads(_b64url_decode(parts[0]))
    payload = json.loads(_b64url_decode(parts[1]))
    sig = _b64url_decode(parts[2])
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    return header, payload, sig, signing_input


def _verify_sig(signing_input: bytes, sig: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(get_public_key_bytes()).verify(sig, signing_input)
        return True
    except InvalidSignature:
        return False


def verify_ucan(token: str, *, expected_audience: str | None = None,
                revoked_dids: set[str] | None = None, _depth: int = 0) -> dict:
    """Verify a UCAN 0.10.0 JWT + its embedded proof chain. Returns a structured result."""
    revoked_dids = revoked_dids or set()
    errors: list[str] = []
    checks: dict[str, bool] = {}
    try:
        header, payload, sig, signing_input = _decode_token(token)
    except Exception as e:
        return {"valid": False, "errors": [f"decode: {e}"], "checks": {}}

    checks["header_typ_alg"] = header.get("typ") == "JWT" and header.get("alg") == "EdDSA"
    if header.get("alg") == "none":
        errors.append("alg 'none' forbidden")
    if not checks["header_typ_alg"]:
        errors.append("header must be {alg:EdDSA, typ:JWT}")

    checks["signature"] = _verify_sig(signing_input, sig)
    if not checks["signature"]:
        errors.append("Ed25519 signature invalid (must verify against issuer JWKS)")

    checks["required_fields"] = all(k in payload for k in ("ucv", "iss", "aud", "cap")) and "exp" in payload
    if not checks["required_fields"]:
        errors.append("missing required field(s): ucv/iss/aud/exp/cap")

    now = _now()
    nbf, exp = payload.get("nbf"), payload.get("exp")
    checks["time_bounds"] = (nbf is None or now >= nbf) and (exp is None or now <= exp)
    if not checks["time_bounds"]:
        errors.append("token not currently valid (nbf/exp)")

    if expected_audience is not None:
        checks["audience"] = payload.get("aud") == expected_audience
        if not checks["audience"]:
            errors.append("aud does not match expected executor DID")

    delegator = (payload.get("fct") or {}).get("delegator")
    if delegator in revoked_dids or payload.get("aud") in revoked_dids:
        checks["not_revoked"] = False
        errors.append("delegator or audience DID is revoked")
    else:
        checks["not_revoked"] = True

    # Proof chain
    proofs = payload.get("prf") or []
    checks["depth_ok"] = _depth < MAX_CHAIN_DEPTH
    if not checks["depth_ok"]:
        errors.append(f"chain exceeds max depth {MAX_CHAIN_DEPTH}")
    if proofs and checks["depth_ok"]:
        chain_ok = True
        for pj in proofs:
            pres = verify_ucan(pj, revoked_dids=revoked_dids, _depth=_depth + 1)
            if not pres["valid"]:
                chain_ok = False
                errors.append("proof invalid: " + "; ".join(pres.get("errors", []))[:200])
                continue
            pp = pres["payload"]
            # iss/aud chain alignment: proof audience == outer delegator
            if pp.get("aud") != delegator:
                chain_ok = False
                errors.append("chain: proof.aud must equal outer delegator")
            # time nesting: proof window >= outer window
            if pp.get("nbf") is not None and nbf is not None and pp["nbf"] > nbf:
                chain_ok = False
                errors.append("chain: proof starts after outer token")
            if pp.get("exp") is not None and (exp is None or pp["exp"] < exp):
                chain_ok = False
                errors.append("chain: proof expires before outer token")
            # attenuation: outer cap must be narrower-or-equal to proof cap
            if not cap_is_narrower(payload.get("cap", {}), pp.get("cap", {})):
                chain_ok = False
                errors.append("chain: capability escalation (attenuation violated)")
        checks["chain"] = chain_ok
    else:
        checks["chain"] = True

    valid = all(v for k, v in checks.items() if k != "audience" or expected_audience is not None) and not errors
    return {"valid": bool(valid), "checks": checks, "errors": errors,
            "payload": payload, "delegator": delegator, "audience": payload.get("aud")}
