"""MolTrust CAEP Profile v1 — Phase 0 endpoint tests.

Covers: emit, pending lookup, ack lifecycle, pagination cursor,
signature roundtrip + tamper detection, JWK format.
"""
import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.caep import (
    emit_caep_event,
    get_pending_events,
    acknowledge_event,
)
from app.signature import (
    sign_payload,
    build_score_signing_payload,
    canonicalize,
)
from app.registry_keys import get_public_jwk, get_public_key_bytes


async def test_emit_and_pending(test_db):
    did = "did:moltrust:test_emit"
    event_id = await emit_caep_event(
        test_db, did, "trust_score_change",
        {"old_score": 85, "new_score": 70},
    )
    assert event_id.startswith("evt_")

    events, has_more = await get_pending_events(test_db, did)
    assert len(events) == 1
    assert events[0]["event_id"] == event_id
    assert has_more is False


async def test_acknowledge(test_db):
    did = "did:moltrust:test_ack"
    event_id = await emit_caep_event(
        test_db, did, "flag_added", {"flag_name": "test_flag"}
    )

    result = await acknowledge_event(test_db, event_id)
    assert result["status"] == "ok"

    result2 = await acknowledge_event(test_db, event_id)
    assert result2["status"] == "already_ack"

    result3 = await acknowledge_event(test_db, "evt_doesnotexist")
    assert result3["status"] == "not_found"

    events, _ = await get_pending_events(test_db, did)
    assert len(events) == 0


async def test_pagination(test_db):
    did = "did:moltrust:test_paginate"
    for i in range(5):
        await emit_caep_event(test_db, did, "trust_score_change", {"i": i})

    events, has_more = await get_pending_events(test_db, did, limit=3)
    assert len(events) == 3
    assert has_more is True

    events2, has_more2 = await get_pending_events(
        test_db, did, limit=3, since_event_id=events[-1]["event_id"]
    )
    assert len(events2) == 2
    assert has_more2 is False


def test_signature_roundtrip():
    payload = build_score_signing_payload(
        did="did:moltrust:test_sig",
        trust_score=85.0,
        computed_at="2026-05-09T05:20:01.000000+00:00",
        valid_until="2026-05-09T06:20:01.000000+00:00",
        policy_version="phase2",
    )
    sig_b64 = sign_payload(payload)
    sig_bytes = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))

    pub_key = Ed25519PublicKey.from_public_bytes(get_public_key_bytes())
    canonical = canonicalize(payload)
    pub_key.verify(sig_bytes, canonical)  # raises on failure


def test_signature_tamper_detected():
    payload = build_score_signing_payload(
        did="did:moltrust:test_tamper",
        trust_score=85.0,
        computed_at="2026-05-09T05:20:01Z",
        valid_until="2026-05-09T06:20:01Z",
        policy_version="phase2",
    )
    sig_b64 = sign_payload(payload)
    sig_bytes = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))

    payload["trust_score"] = 99.0  # tamper
    canonical_tampered = canonicalize(payload)

    pub_key = Ed25519PublicKey.from_public_bytes(get_public_key_bytes())
    with pytest.raises(InvalidSignature):
        pub_key.verify(sig_bytes, canonical_tampered)


def test_jwk_format():
    jwk = get_public_jwk()
    assert jwk["kid"] == "moltrust-registry-2026-v1"
    assert jwk["kty"] == "OKP"
    assert jwk["crv"] == "Ed25519"
    assert jwk["use"] == "sig"
    assert jwk["alg"] == "EdDSA"
    assert len(jwk["x"]) == 43  # 32-byte b64url no-padding

def test_registry_jws_format_and_roundtrip():
    """Compact JWS (header.payload.signature) verifies with stock primitives."""
    import json
    from app.signature import build_registry_jws, build_score_signing_payload

    payload = build_score_signing_payload(
        did="did:moltrust:test_jws",
        trust_score=72.5,
        computed_at="2026-05-25T10:00:00.000000+00:00",
        valid_until="2026-05-25T11:00:00.000000+00:00",
        policy_version="phase2",
    )
    token = build_registry_jws(payload)

    parts = token.split(".")
    assert len(parts) == 3, "compact JWS must be 3-part"

    pad = lambda s: s + "=" * (-len(s) % 4)
    header = json.loads(base64.urlsafe_b64decode(pad(parts[0])))
    assert header == {"alg": "EdDSA", "typ": "JWT", "kid": "moltrust-registry-2026-v1"}

    decoded_payload = json.loads(base64.urlsafe_b64decode(pad(parts[1])))
    assert decoded_payload["did"] == "did:moltrust:test_jws"
    assert decoded_payload["trust_score"] == 72.5

    sig_bytes = base64.urlsafe_b64decode(pad(parts[2]))
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")

    pub_key = Ed25519PublicKey.from_public_bytes(get_public_key_bytes())
    pub_key.verify(sig_bytes, signing_input)  # raises on failure


def test_registry_jws_pyjwt_compat():
    """Stock PyJWT verifier (out-of-the-box) accepts our JWS against JWK."""
    import jwt as pyjwt
    from app.signature import build_registry_jws, build_score_signing_payload
    from app.registry_keys import get_public_jwk

    payload = build_score_signing_payload(
        did="did:moltrust:test_pyjwt",
        trust_score=88.0,
        computed_at="2026-05-25T10:00:00.000000+00:00",
        valid_until="2026-05-25T11:00:00.000000+00:00",
        policy_version="phase2",
    )
    token = build_registry_jws(payload)

    jwk = get_public_jwk()
    pub_key = pyjwt.PyJWK(jwk).key
    decoded = pyjwt.decode(
        token, pub_key, algorithms=["EdDSA"], options={"verify_exp": False}
    )
    assert decoded["did"] == "did:moltrust:test_pyjwt"
    assert decoded["trust_score"] == 88.0

def test_registry_jws_survives_scrub_secrets():
    """JWT-shape allowlisted field passes scrubber unmodified; unknown JWT-shape still scrubbed."""
    from app.main import scrub_secrets
    sample_jws = "eyJhbGc.eyJzdWI.signature_bytes"
    response = {
        "did": "did:moltrust:test",
        "trust_score": 80,
        "registry_signature": "bare_b64url_no_dots",
        "registry_jws": sample_jws,                      # allowlisted -> passes through
        "leaked_token": "eyJhbGc.eyJzdWI.scrubbed",      # unknown field -> still scrubbed
    }
    scrubbed = scrub_secrets(response)
    assert scrubbed["registry_jws"] == sample_jws, \
        f"registry_jws was scrubbed: {scrubbed['registry_jws']!r}"
    assert scrubbed["leaked_token"] == "[REDACTED]", \
        "JWT-shape value in unknown field should still be scrubbed"
    assert scrubbed["registry_signature"] == "bare_b64url_no_dots"
    assert scrubbed["did"] == "did:moltrust:test"
    assert scrubbed["trust_score"] == 80


def test_scrub_secrets_default_behaviour_unchanged():
    """Allowlist does not weaken default scrubbing of unknown-field values."""
    from app.main import scrub_secrets
    leaked = {
        "x": "eyJhbGc.eyJzdWI.aaaa",
        "y": "ghp_abcdefghijklmnopqrstuvwxyz0123456789ab",
    }
    out = scrub_secrets(leaked)
    assert out["x"] == "[REDACTED]"
    assert out["y"] == "[REDACTED]"

