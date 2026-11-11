"""JWS-sign the public agent card (A2A v1.0.1 AgentCardSignature shape).

The signature lives as a separate `signatures[]` entry on the card —
NOT a compact 3-part JWS string. `protected` is the base64url-encoded
JCS-canonical JWS header. `signature` is the base64url-encoded raw
Ed25519 signature over `f"{protected}.{payload}"` where `payload` is
the base64url-encoded JCS-canonical card MINUS its `signatures` field.

Verifier-side roundtrip in these tests uses the public key loaded
directly from `registry_keys` — the same key whose JWK is published at
`/.well-known/registry-key.json`. Any standard JOSE verifier with
that JWK can do the same.
"""
import base64
import json

import pytest

from app.signature import canonicalize, sign_agent_card
from app.registry_keys import REGISTRY_KID, get_private_key


SAMPLE_CARD = {
    "name": "MolTrust Trust Registry",
    "description": "Test card",
    "version": "1.0.1",
    "supportedInterfaces": [{"url": "https://api.moltrust.ch"}],
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text", "data"],
    "skills": [{"id": "trust-score", "name": "Agent Trust Score"}],
}


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _verify_signed_card(signed: dict) -> bool:
    """Re-derive the signing input from the signed card and verify it.

    Returns True iff every signature in `signed["signatures"]` verifies
    against the canonical card minus the signatures field.
    """
    assert "signatures" in signed and signed["signatures"], "no signatures present"
    card_without_sigs = {k: v for k, v in signed.items() if k != "signatures"}
    payload_b64 = base64.urlsafe_b64encode(canonicalize(card_without_sigs)).rstrip(b"=").decode("ascii")
    public_key = get_private_key().public_key()
    for entry in signed["signatures"]:
        signing_input = f"{entry['protected']}.{payload_b64}".encode("ascii")
        sig = _b64url_decode(entry["signature"])
        public_key.verify(sig, signing_input)  # raises InvalidSignature on failure
    return True


# ---------------------------------------------------------------------------
# 1. Sign + verify roundtrip
# ---------------------------------------------------------------------------

def test_sign_then_verify_roundtrip():
    signed = sign_agent_card(SAMPLE_CARD)
    assert _verify_signed_card(signed) is True


# ---------------------------------------------------------------------------
# 2. Idempotency — same card content yields the same signature
# ---------------------------------------------------------------------------

def test_sign_is_deterministic():
    a = sign_agent_card(SAMPLE_CARD)
    b = sign_agent_card(SAMPLE_CARD)
    assert a["signatures"][0]["protected"] == b["signatures"][0]["protected"]
    assert a["signatures"][0]["signature"] == b["signatures"][0]["signature"], (
        "Ed25519 over deterministic input is deterministic — same card → same sig"
    )


# ---------------------------------------------------------------------------
# 3. Tamper detection — mutate the card after signing, verify must fail
# ---------------------------------------------------------------------------

def test_tampered_card_fails_verify():
    from cryptography.exceptions import InvalidSignature

    signed = sign_agent_card(SAMPLE_CARD)
    signed["description"] = "tampered"
    with pytest.raises(InvalidSignature):
        _verify_signed_card(signed)


# ---------------------------------------------------------------------------
# 4. Strip-signatures-before-sign — re-signing a card that already has
#    signatures must produce the same result as signing the unsigned card,
#    otherwise the signature would cover itself recursively.
# ---------------------------------------------------------------------------

def test_existing_signatures_are_stripped_before_signing():
    first = sign_agent_card(SAMPLE_CARD)
    # Re-sign the already-signed card: must IGNORE the existing signatures.
    second = sign_agent_card(first)
    assert second["signatures"][0]["signature"] == first["signatures"][0]["signature"], (
        "Re-signing must produce the same signature as signing the unsigned "
        "card — proves existing signatures[] is stripped before canonicalization"
    )
    # And the re-signed card must verify against the public key.
    assert _verify_signed_card(second) is True


# ---------------------------------------------------------------------------
# 5. Protected header sanity — alg=EdDSA, kid=REGISTRY_KID, typ=a2a-card+jws
# ---------------------------------------------------------------------------

def test_protected_header_shape():
    signed = sign_agent_card(SAMPLE_CARD)
    header_b64 = signed["signatures"][0]["protected"]
    header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
    assert header["alg"] == "EdDSA", "Ed25519 is EdDSA in JOSE"
    assert header["kid"] == REGISTRY_KID, f"kid must be {REGISTRY_KID}"
    assert header["typ"] == "a2a-card+jws", "A2A spec media type"


# ---------------------------------------------------------------------------
# 6. Augmentation-after-signing drift regression — catches the PR #99 bug
#    that this hotfix repairs.
#
#    PR #99 placed the signing inside _load_public_agent_card (sign once
#    at cache-load), but `/extendedAgentCard` then *augmented* the cached
#    card with endorsement + MoltGuard fields before returning. The
#    delivered card therefore differed from what was signed, and any
#    external verifier rejected the signature.
#
#    Pure-unit test (no TestClient): simulate the two flows side-by-side.
# ---------------------------------------------------------------------------

def test_buggy_sign_before_augment_drift_is_caught_by_verify():
    """Reproduce the PR #99 bug pattern: sign first, augment second.
    A consumer verifying the augmented response MUST reject the signature
    because the canonicalized payload no longer matches what was signed."""
    from cryptography.exceptions import InvalidSignature

    base = dict(SAMPLE_CARD)
    signed_pre = sign_agent_card(base)
    # Simulate /extendedAgentCard augmenting AFTER the loader-side sign
    # — exactly what PR #99 did with `**public_card` + skill append.
    augmented_pre = {
        **signed_pre,
        "skills": signed_pre.get("skills", []) + [{"id": "endorsement"}],
    }
    with pytest.raises(InvalidSignature):
        _verify_signed_card(augmented_pre)


def test_correct_augment_then_sign_verifies():
    """Reproduce the hotfix pattern: augment first, sign on the final
    body. Verifier must accept."""
    base = dict(SAMPLE_CARD)
    augmented_first = {
        **base,
        "skills": base.get("skills", []) + [{"id": "endorsement"}],
    }
    signed_post = sign_agent_card(augmented_first)
    assert _verify_signed_card(signed_post) is True


# ---------------------------------------------------------------------------
# 7. Middleware regression — scrub_secrets must NOT mutate JWS signature
#    fields. Goes through the full request pipeline (TestClient), unlike the
#    pure-unit tests above. Caught the 2026-05-31 root cause: the
#    `content_filter_middleware` rewrote every response JSON via
#    `scrub_secrets`, and the SENSITIVE_PATTERNS matched the base64 JWS
#    signature, replacing the middle of it with the literal `[REDACTED]`.
#    Once `protected` + `signature` joined `_KNOWN_PUBLIC_CREDENTIAL_FIELDS`,
#    the field values pass through unchanged.
# ---------------------------------------------------------------------------

async def test_signature_field_passes_through_middleware(
    async_client, credit_test_agent, monkeypatch,
):
    """The signed `/extendedAgentCard` response must reach the consumer
    without `scrub_secrets` mutating the base64 signature or the
    base64 protected header. Any `[REDACTED]` in either field — or any
    length other than the b64url-encoded 64-byte Ed25519 sig — is a
    middleware drift, not a code bug in `sign_agent_card`."""
    import base64

    # The endpoint reads the unsigned card from the web root, which exists on
    # the box only. The repository copy is the source the web root is deployed
    # from (CLAUDE.md, Discovery-Checklist), so the test serves that one.
    import json
    from pathlib import Path
    import app.main as main
    repo_card = Path(__file__).resolve().parents[1] / ".well-known" / "agent-card.json"
    monkeypatch.setattr(main, "_load_public_agent_card",
                        lambda: json.loads(repo_card.read_text()))

    _, api_key = await credit_test_agent(balance=1)
    r = await async_client.get(
        "/extendedAgentCard", headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200, r.text
    card = r.json()
    assert card.get("signatures"), "response carries no signatures field"
    sig = card["signatures"][0]

    assert "[REDACTED]" not in sig["signature"], (
        "scrub_secrets corrupted the signature value. Add 'signature' to "
        "_KNOWN_PUBLIC_CREDENTIAL_FIELDS."
    )
    assert "[REDACTED]" not in sig["protected"], (
        "scrub_secrets corrupted the protected header. Add 'protected' to "
        "_KNOWN_PUBLIC_CREDENTIAL_FIELDS."
    )
    raw = base64.urlsafe_b64decode(sig["signature"] + "=" * (-len(sig["signature"]) % 4))
    assert len(raw) == 64, (
        f"Ed25519 signature must be 64 bytes; got {len(raw)} — middleware drift"
    )
