"""The committed public agent card must verify for a stranger.

Why this file exists next to ``test_a2a_card_signing.py``: that suite verifies
signatures by calling ``app.signature.canonicalize`` — the same function used to
produce them. It proves the signer is self-consistent and nothing more. It was
green throughout 2026-09-20, the day the served card carried a signature that no
external verifier accepted, because nothing tested the artifact that ships.

This suite closes both halves of that gap:

  * verification runs through ``lib.agent_card_verify``, an independent RFC 8785
    implementation, against the JWK as published at
    ``/.well-known/registry-key.json``;
  * the subject is the committed ``.well-known/agent-card.json`` — the exact
    bytes deployed to the web root — not a synthetic sample.

A hand edit to the committed card therefore fails CI instead of reaching
production. Drift of the *deployed* copy is a separate concern and belongs to
``agents/watchdog.py::check_agent_card_signature``.
"""

import base64
import json
import os

import pytest

from lib.agent_card_verify import (
    CardVerificationError,
    canonicalize_rfc8785,
    verify_agent_card,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD_PATH = os.path.join(REPO_ROOT, ".well-known", "agent-card.json")
KEY_PATH = os.path.join(REPO_ROOT, ".well-known", "registry-key.json")


@pytest.fixture(scope="module")
def card() -> dict:
    with open(CARD_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def jwk() -> dict:
    with open(KEY_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# The canonicalizer itself, against the RFC rather than against our signer
# ---------------------------------------------------------------------------

def test_rfc8785_appendix_b_property_sorting():
    """RFC 8785 sorts object keys by UTF-16 code unit, not by code point.

    The two orders differ only above the BMP, which is exactly where a naive
    implementation looks correct: U+1F602 sorts *before* U+FB33 because its
    leading surrogate is D83D. Sorting Python strings natively puts it after.
    """
    value = {
        "\u20ac": "Euro Sign",
        "\r": "Carriage Return",
        "\ufb33": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\u0080": "Control",
        "\u00f6": "Latin Small Letter O With Diaeresis",
        "\U0001f602": "Emoji: Face With Tears of Joy",
        "\u03a9": "Greek Capital Letter Omega",
    }
    expected = (
        '{"\\r":"Carriage Return",'
        '"1":"One",'
        '"\u0080":"Control",'
        '"\u00f6":"Latin Small Letter O With Diaeresis",'
        '"\u03a9":"Greek Capital Letter Omega",'
        '"\u20ac":"Euro Sign",'
        '"\U0001f602":"Emoji: Face With Tears of Joy",'
        '"\ufb33":"Hebrew Letter Dalet With Dagesh"}'
    ).encode("utf-8")
    assert canonicalize_rfc8785(value) == expected


def test_rfc8785_escapes_only_what_the_spec_escapes():
    """Two-character escapes for the named controls, \\u00xx below 0x20, and
    nothing else — non-ASCII stays literal UTF-8."""
    value = {"a": "\b\t\n\f\r\"\\", "b": "\u0001", "c": "\u00e4\u20ac"}
    assert canonicalize_rfc8785(value) == (
        '{"a":"\\b\\t\\n\\f\\r\\"\\\\","b":"\\u0001","c":"\u00e4\u20ac"}'
    ).encode("utf-8")


def test_independent_canonicalizer_agrees_with_the_signer(card):
    """Cross-check the two implementations on the real card body.

    Not the primary assertion — agreement with the signer is what the old suite
    already assumed. It is here so that a divergence is reported as a
    canonicalization difference rather than surfacing only as a failed
    signature, which is much harder to read.
    """
    jcs = pytest.importorskip("jcs", reason="signer-side canonicalizer not installed")
    body = {k: v for k, v in card.items() if k != "signatures"}
    assert canonicalize_rfc8785(body) == jcs.canonicalize(body)


# ---------------------------------------------------------------------------
# The shipped artifact
# ---------------------------------------------------------------------------

def test_committed_card_verifies_against_published_key(card, jwk):
    header = verify_agent_card(card, jwk)
    assert header["alg"] == "EdDSA"
    assert header["typ"] == "a2a-card+jws"
    assert header["kid"] == jwk["kid"]


def test_tampering_with_the_committed_card_is_detected(card, jwk):
    tampered = {**card, "description": card.get("description", "") + " "}
    with pytest.raises(CardVerificationError, match="modified after it was signed"):
        verify_agent_card(tampered, jwk)


def test_signature_is_a_64_byte_ed25519_signature(card):
    raw_b64 = card["signatures"][0]["signature"]
    raw = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
    assert len(raw) == 64


# ---------------------------------------------------------------------------
# Content invariants — a signed card that is wrong is still wrong
# ---------------------------------------------------------------------------

def _all_mode_lists(card: dict):
    for field in ("defaultInputModes", "defaultOutputModes"):
        if field in card:
            yield field, card[field]
    for skill in card.get("skills", []):
        for field in ("inputModes", "outputModes"):
            if field in skill:
                yield f"skills[{skill.get('id')}].{field}", skill[field]


def test_every_mode_is_a_media_type(card):
    """A2A declares these fields as media types. The card shipped the part-kind
    names "text" and "data", which resolve to nothing."""
    offenders = [
        (where, mode)
        for where, modes in _all_mode_lists(card)
        for mode in modes
        if "/" not in mode
    ]
    assert not offenders, f"not media types: {offenders}"


def test_x402_facilitator_settles_the_network_we_invoice(card):
    """We invoice on Base mainnet, so the named facilitator must be one that
    settles it. The card used to name our own API, which has no facilitator
    interface at all (no /supported)."""
    from scripts.resign_agent_card import X402_EXTENSION_URI, X402_FACILITATOR

    extensions = card["capabilities"]["extensions"]
    x402 = next(e for e in extensions if e["uri"] == X402_EXTENSION_URI)
    assert x402["params"]["facilitator"] == X402_FACILITATOR
    assert x402["params"]["chain"] == "eip155:8453"


def test_normalization_is_a_fixed_point(card):
    """Running the normalizer over the committed card must change nothing.

    If it would, the committed card was produced by hand rather than by the
    script, and the next run of the script would silently rewrite it.
    """
    from scripts.resign_agent_card import normalize

    _normalized, changes = normalize({k: v for k, v in card.items() if k != "signatures"})
    assert changes == [], f"committed card is not normalized: {changes}"
