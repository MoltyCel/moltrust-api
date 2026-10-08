"""The did:moltrust identifier derivation of method specification section 2.2.

From specification v0.2 an identifier is the first 8 bytes of SHA-256 over the
agent's raw 32-byte Ed25519 public key, written as 16 lowercase hex characters
(rule `derived-sha256-ed25519-8`). Identifiers issued before v0.2 are assigned
and opaque (rule `assigned-opaque`): they were minted from uuid4().hex[:16],
they stay resolvable, and nothing is promised about how they relate to any key.

Pure helpers with no app or db imports, so they test in isolation.

Only a path that has proved possession of the key may derive from it. A route
that takes a public key on trust would let anyone occupy the identifier of a
key they do not hold, so /identity/register-pop derives and the keyless routes
do not.
"""

from __future__ import annotations

import hashlib
import re

RULE_DERIVED = "derived-sha256-ed25519-8"
RULE_ASSIGNED = "assigned-opaque"

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def derive_method_specific_id(public_key_hex: str) -> str:
    """Section 2.2: the first 8 bytes of SHA-256 over the raw public key."""
    raw = bytes.fromhex(public_key_hex)
    if len(raw) != 32:
        raise ValueError(f"an Ed25519 public key is 32 bytes, got {len(raw)}")
    return hashlib.sha256(raw).digest()[:8].hex()


def derive_did(public_key_hex: str) -> str:
    return "did:moltrust:" + derive_method_specific_id(public_key_hex)


def identifier_rule(did: str, public_key_hex: str | None) -> str | None:
    """The rule a registered identifier was issued under, read from its bytes.

    `derived-sha256-ed25519-8` when the identifier recomputes from the key on
    record, `assigned-opaque` when it is sixteen hex characters and does not,
    None for anything outside the section 2.2 syntax (pre-convention
    identifiers, the `ext_` bridge form). Recomputed rather than stored, so it
    needs no column and cannot drift from the key it describes.

    An assigned identifier recomputes from its key only by a 2^-64 chance; the
    registry refuses to issue a derived identifier equal to an assigned one,
    so the two rules cannot name the same identifier.
    """
    prefix = "did:moltrust:"
    if not did.startswith(prefix):
        return None
    msi = did[len(prefix):]
    if not _HEX16.match(msi):
        return None
    if public_key_hex:
        try:
            if derive_method_specific_id(public_key_hex.lower()) == msi:
                return RULE_DERIVED
        except ValueError:
            pass
    return RULE_ASSIGNED
