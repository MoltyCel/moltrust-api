"""Independent verifier for the public A2A agent card signature.

Deliberately shares NO code with ``app/signature.py``. The signing side
canonicalizes with the ``jcs`` package; this module implements RFC 8785 from
the specification text instead. That is the whole point: the previous test
suite verified signatures by calling the same ``canonicalize`` used to produce
them, so a canonicalization change on both sides would have stayed green while
every third-party verifier rejected the card. It did not catch the live
breakage of 2026-09-20 either, because nothing tested the *served* artifact.

What a consumer actually has is a card document and the JWK published at
``/.well-known/registry-key.json``. This module takes exactly those two inputs
and nothing else, so a green result here means a stranger can verify us.

Only the Ed25519 primitive comes from a library (``cryptography``); the
canonicalization, the payload construction and the header checks are written
here against the RFCs.

Entry point: ``verify_agent_card(card, jwk)`` — raises ``CardVerificationError``
with a specific reason, returns the verified protected header on success.
"""

import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

__all__ = [
    "CardVerificationError",
    "canonicalize_rfc8785",
    "verify_agent_card",
]


class CardVerificationError(Exception):
    """The card does not verify, with the reason in the message."""


# ---------------------------------------------------------------------------
# RFC 8785 (JCS) — written from the spec, not imported
# ---------------------------------------------------------------------------

# RFC 8785 section 3.2.2.2: these get two-character escapes, every other
# control character below 0x20 gets \u00xx, and nothing else is escaped.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _json_string(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        esc = _SHORT_ESCAPES.get(cp)
        if esc is not None:
            out.append(esc)
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _number(value) -> str:
    """Serialize a number per RFC 8785 section 3.2.2.3.

    That section defers to ECMAScript ``Number::toString``, which is a
    non-trivial shortest-roundtrip algorithm. Rather than approximate it and
    produce a canonical form that is subtly wrong, this refuses anything that
    is not an exact integer. The agent card carries no fractional numbers, so
    the restriction costs nothing — and a future card that grows one will fail
    loudly here instead of silently signing an uncanonical payload.
    """
    if isinstance(value, bool):  # bool is an int subclass — must precede it
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    raise CardVerificationError(
        f"non-integer number {value!r}: RFC 8785 number canonicalization "
        "(ECMAScript Number::toString) is not implemented here"
    )


def _serialize(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _json_string(value)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize(v) for v in value) + "]"
    if isinstance(value, dict):
        # RFC 8785 section 3.2.3: sort by UTF-16 code units. Python's native
        # str ordering is by code point, which differs from UTF-16 ordering
        # for anything above the BMP. Encoding to UTF-16BE and comparing the
        # bytes gives the code-unit order the spec asks for.
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        return "{" + ",".join(f"{_json_string(k)}:{_serialize(v)}" for k, v in items) + "}"
    raise CardVerificationError(f"cannot canonicalize {type(value).__name__}")


def canonicalize_rfc8785(value) -> bytes:
    """Return the RFC 8785 canonical UTF-8 serialization of ``value``."""
    return _serialize(value).encode("utf-8")


# ---------------------------------------------------------------------------
# base64url (RFC 7515 section 2 — unpadded)
# ---------------------------------------------------------------------------

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception as exc:  # noqa: BLE001 — surfaced as a verification reason
        raise CardVerificationError(f"not valid base64url: {exc}") from exc


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _public_key_from_jwk(jwk: dict) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise CardVerificationError(
            f"unsupported JWK: kty={jwk.get('kty')!r} crv={jwk.get('crv')!r}, "
            "expected OKP/Ed25519"
        )
    raw = _b64url_decode(jwk.get("x") or "")
    if len(raw) != 32:
        raise CardVerificationError(f"Ed25519 public key must be 32 bytes, got {len(raw)}")
    return Ed25519PublicKey.from_public_bytes(raw)


def verify_agent_card(card: dict, jwk: dict) -> dict:
    """Verify the card's detached JWS against ``jwk``.

    ``card`` is the served document including ``signatures``; ``jwk`` is the
    published verification key. Returns the decoded protected header of the
    signature that verified. Raises ``CardVerificationError`` otherwise.
    """
    signatures = card.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise CardVerificationError("card carries no signatures[]")

    body = {k: v for k, v in card.items() if k != "signatures"}
    payload_b64 = _b64url_encode(canonicalize_rfc8785(body))
    public_key = _public_key_from_jwk(jwk)

    errors = []
    for index, entry in enumerate(signatures):
        if not isinstance(entry, dict) or "protected" not in entry or "signature" not in entry:
            errors.append(f"signatures[{index}]: missing protected/signature")
            continue
        try:
            header = json.loads(_b64url_decode(entry["protected"]))
        except (ValueError, CardVerificationError) as exc:
            errors.append(f"signatures[{index}]: undecodable protected header ({exc})")
            continue
        if header.get("alg") != "EdDSA":
            errors.append(f"signatures[{index}]: alg={header.get('alg')!r}, expected EdDSA")
            continue
        if jwk.get("kid") and header.get("kid") != jwk.get("kid"):
            errors.append(
                f"signatures[{index}]: kid={header.get('kid')!r} does not match "
                f"published key {jwk.get('kid')!r}"
            )
            continue
        signing_input = f"{entry['protected']}.{payload_b64}".encode("ascii")
        try:
            public_key.verify(_b64url_decode(entry["signature"]), signing_input)
        except InvalidSignature:
            errors.append(
                f"signatures[{index}]: signature does not cover this card body "
                "— the document was modified after it was signed"
            )
            continue
        return header

    raise CardVerificationError("; ".join(errors))
