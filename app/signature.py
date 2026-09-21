"""RFC 8785 (JCS) canonical JSON + Ed25519 signing for registry receipts."""
import base64
import jcs

from app.registry_keys import REGISTRY_KID, get_private_key


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def canonicalize(payload: dict) -> bytes:
    """RFC 8785 JSON Canonicalization Scheme — returns UTF-8 bytes."""
    return jcs.canonicalize(payload)


def sign_payload(payload: dict) -> str:
    """Sign payload with registry private key. Returns base64url-encoded signature."""
    sig = get_private_key().sign(canonicalize(payload))
    return _b64url_encode(sig)


def build_registry_jws(payload: dict, kid: str = REGISTRY_KID) -> str:
    """Compact JWS (RFC 7515) over JCS-canonicalised payload, EdDSA/Ed25519.

    Returns three-part dot-separated token verifiable by any stock JOSE library
    against the public JWK published in /.well-known/jwks.json.
    """
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    header_b64 = _b64url_encode(canonicalize(header))
    payload_b64 = _b64url_encode(canonicalize(payload))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = get_private_key().sign(signing_input)
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def build_score_signing_payload(
    did: str,
    trust_score: float,
    computed_at: str,
    valid_until: str,
    policy_version: str,
) -> dict:
    """Deterministic minimal payload signed for trust-score responses.

    This is the v1 payload and it stays exactly as it is. `registry_signature`
    is a *detached* signature, so a verifier has to rebuild these five fields
    from the response to check it; adding a field here would break every one of
    them. The gate attestation adds its fields in `build_gate_payload` instead,
    and travels as a compact JWS where the payload is read rather than rebuilt.
    """
    return {
        "did": did,
        "trust_score": trust_score,
        "computed_at": computed_at,
        "valid_until": valid_until,
        "policy_version": policy_version,
    }


GATE_PAYLOAD_VERSION = 2


def build_gate_payload(
    did: str,
    public_key: str,
    trust_score: float,
    withheld: bool,
    credential_types: list,
    computed_at: str,
    valid_until: str,
    policy_version: str,
) -> dict:
    """Everything a gate needs to decide, offline, in one signed statement.

    The v1 payload cannot carry a gate. Three things are missing from it and
    each one alone is disqualifying:

    * **No public key.** `did:moltrust:<hex>` is a random identifier, not a
      hash of a key, so nothing in the v1 payload binds the DID to the keypair
      that is supposed to prove control of it. A verifier could check that a
      score was issued for some DID and never that the caller is that DID.
      `/identity/key/{did}` answers it, but only online and unsigned.
    * **No `withheld`.** A withheld score serialises as `trust_score: null`,
      and a verifier reading only the v1 payload cannot tell "we have not
      evaluated this agent" from "the field is missing". Those must not
      collapse into the same decision.
    * **No credential types.** `require_moltrust(credential_type=…)` has
      nothing signed to check against.

    So the gate reads this payload, and it reads it out of a compact JWS —
    where the bytes that were signed travel with the signature and nothing is
    rebuilt. Adding a field here is additive for every verifier; `v` is present
    so one that cares can say which shape it got.
    """
    return {
        "v": GATE_PAYLOAD_VERSION,
        "did": did,
        "public_key": public_key,
        "trust_score": trust_score,
        "withheld": bool(withheld),
        "credential_types": sorted(set(credential_types or [])),
        "computed_at": computed_at,
        "valid_until": valid_until,
        "policy_version": policy_version,
    }


def sign_agent_card(card: dict, kid: str = REGISTRY_KID) -> dict:
    """Return `card` augmented with an A2A v1.0.1 `signatures[]` entry.

    Per the A2A spec, AgentCardSignature is `{protected, signature}` —
    NOT a compact JWS string. `protected` is the base64url JCS-canonical
    JWS header; `signature` is the base64url raw Ed25519 signature over
    `signing_input = f"{protected_b64}.{payload_b64}"` (RFC 7515 §5.1).

    The payload is the AgentCard with any existing `signatures` field
    stripped — otherwise the signature would cover itself recursively.
    """
    card_to_sign = {k: v for k, v in card.items() if k != "signatures"}
    payload_b64 = _b64url_encode(canonicalize(card_to_sign))

    header = {"alg": "EdDSA", "kid": kid, "typ": "a2a-card+jws"}
    protected_b64 = _b64url_encode(canonicalize(header))

    signing_input = f"{protected_b64}.{payload_b64}".encode("ascii")
    sig = get_private_key().sign(signing_input)
    signature_b64 = _b64url_encode(sig)

    return {
        **card_to_sign,
        "signatures": [
            {"protected": protected_b64, "signature": signature_b64},
        ],
    }
