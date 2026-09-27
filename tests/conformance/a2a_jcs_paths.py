"""The three RFC 8785 paths in this repository, as functions a conformance runner can score.

The a2a-jcs-v01 runner (a2aproject/a2a-tck#228) scores a function against the
target it provides: ``rfc8785`` for a primitive that turns one JSON value into
canonical bytes, and ``card-signing-input`` for the bytes an Agent Card
signature covers. This repository has three such paths and they are scored
separately, because one number for three paths leaves open which was measured:

* ``canonicalize``: ``app.signature.canonicalize``, the primitive that registry
  receipts and envelopes sign through. Target ``rfc8785``. It does not apply the
  A2A rule that drops ``signatures``; its callers do.
* ``signing_path``: ``app.signature.sign_agent_card``, called for real. The
  bytes it hands to Ed25519 are recorded at the key, so the rule-3 strip, the
  canonicalization and the JWS assembly are all the repository's own code.
* ``verify_path``: ``lib.agent_card_verify.verify_agent_card``, called for real,
  with the bytes recorded at the public key the same way.

Nothing here reimplements a rule. The only thing each adapter adds is a key that
records what it was asked to sign or verify instead of doing cryptography, and
the payload is read back out of the JWS signing input
``BASE64URL(protected) || "." || BASE64URL(payload)`` (RFC 7515 section 5.1).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from app import signature
from lib import agent_card_verify


def canonicalize(value: Any) -> bytes:
    """The repository's RFC 8785 primitive, unchanged."""
    return signature.canonicalize(value)


def _payload_of(signing_input: bytes) -> bytes:
    """Return the payload bytes a JWS signing input carries."""
    _protected, payload_b64 = signing_input.split(b".")
    return base64.urlsafe_b64decode(payload_b64 + b"=" * (-len(payload_b64) % 4))


class _RecordingPrivateKey:
    """Stands in for the registry key and keeps what it was asked to sign."""

    def __init__(self) -> None:
        self.seen: list[bytes] = []

    def sign(self, data: bytes) -> bytes:
        self.seen.append(data)
        return bytes(64)


def signing_path(card: dict) -> bytes:
    """The bytes ``sign_agent_card`` signs for ``card``."""
    key = _RecordingPrivateKey()
    original = signature.get_private_key
    signature.get_private_key = lambda: key
    try:
        signature.sign_agent_card(card)
    finally:
        signature.get_private_key = original
    (signing_input,) = key.seen
    return _payload_of(signing_input)


class _RecordingPublicKey:
    """Stands in for the published key and keeps what it was asked to verify."""

    def __init__(self) -> None:
        self.seen: list[bytes] = []

    def verify(self, _signature: bytes, data: bytes) -> None:
        self.seen.append(data)


_PLACEHOLDER_ENTRY = {
    "protected": base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA"}).encode())
    .rstrip(b"=")
    .decode(),
    "signature": "AA",
}


def verify_path(card: dict) -> bytes:
    """The bytes ``verify_agent_card`` checks a signature of ``card`` against.

    The bytes are taken where ``verify_agent_card`` canonicalizes the card body,
    which it does once, after dropping ``signatures`` and before it reads any
    signature entry. So a card whose own entries fail the header checks (the
    rule-3 vectors carry an ES256 entry) is still measured, unchanged. When a
    signature entry does reach the key, the payload it carries is checked
    against the recorded bytes, so the two readings cannot drift apart.

    ``verify_agent_card`` refuses a card with no signature before it
    canonicalizes anything, which is right for a verifier and leaves nothing to
    score. For a card with no non-empty ``signatures`` list, one placeholder
    entry is supplied under the key the rule strips, so a correct verifier
    produces the same bytes either way. The cost is stated rather than hidden:
    on the reject vector whose ``signatures`` is an empty array, a verifier that
    failed to strip would show the placeholder instead of ``[]`` and still
    differ from the presented bytes, so that one vector cannot catch a missing
    strip on this path. The other three rule-3 vectors can.
    """
    presented = card
    entries = card.get("signatures")
    if not (isinstance(entries, list) and entries):
        presented = {**card, "signatures": [_PLACEHOLDER_ENTRY]}

    recorded: list[bytes] = []
    key = _RecordingPublicKey()
    original_canonicalize = agent_card_verify.canonicalize_rfc8785
    original_key = agent_card_verify._public_key_from_jwk

    def recording_canonicalize(value: Any) -> bytes:
        out = original_canonicalize(value)
        recorded.append(out)
        return out

    agent_card_verify.canonicalize_rfc8785 = recording_canonicalize
    agent_card_verify._public_key_from_jwk = lambda _jwk: key
    try:
        agent_card_verify.verify_agent_card(presented, {"kty": "OKP", "crv": "Ed25519"})
    except agent_card_verify.CardVerificationError:
        if not recorded:
            raise
    finally:
        agent_card_verify.canonicalize_rfc8785 = original_canonicalize
        agent_card_verify._public_key_from_jwk = original_key
    (body,) = recorded
    for signing_input in key.seen:
        if _payload_of(signing_input) != body:
            raise AssertionError(
                "verify_agent_card verified bytes other than the body it canonicalized"
            )
    return body


def signing_path_without_strip(card: dict) -> bytes:
    """Negative control: the signing path with the rule-3 strip disabled.

    Not an implementation of anything. The workflow runs it to show the runner
    fails when the strip is missing; a green run means something only once the
    same harness has been seen to go red.
    """
    original = signature.sign_agent_card

    def keep_signatures(c: dict, kid: str = signature.REGISTRY_KID) -> dict:
        payload_b64 = signature._b64url_encode(signature.canonicalize(c))
        header = {"alg": "EdDSA", "kid": kid, "typ": "a2a-card+jws"}
        protected_b64 = signature._b64url_encode(signature.canonicalize(header))
        signature.get_private_key().sign(
            f"{protected_b64}.{payload_b64}".encode("ascii")
        )
        return c

    signature.sign_agent_card = keep_signatures
    try:
        return signing_path(card)
    finally:
        signature.sign_agent_card = original
