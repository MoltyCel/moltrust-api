"""The 422 for a mis-encoded public_key has to say what the encoding is.

Reported on 2026-09-21 and paid as a bounty: posting a base64 key to
/identity/register-pop answered "String should have at least 64 characters",
which is true of 44 characters of base64 and tells the caller nothing.
"""
import base64
import os

import pytest

from app.keyless_register import normalise_public_key


RAW = os.urandom(32)


def test_lowercase_hex_passes_through():
    assert normalise_public_key(RAW.hex()) == RAW.hex()


def test_uppercase_hex_is_accepted_and_normalised():
    assert normalise_public_key(RAW.hex().upper()) == RAW.hex()


def test_surrounding_whitespace_is_stripped():
    assert normalise_public_key(f"  {RAW.hex()}\n") == RAW.hex()


@pytest.mark.parametrize("value", [
    base64.b64encode(RAW).decode(),
    base64.urlsafe_b64encode(RAW).decode().rstrip("="),
    "0x" + RAW.hex(),
    "z" * 64,
    RAW.hex()[:63],
    "",
])
def test_other_encodings_are_rejected_by_name(value):
    with pytest.raises(ValueError) as exc:
        normalise_public_key(value)
    message = str(exc.value)
    assert "64 hex characters" in message
    assert "base64" in message


def test_length_mismatch_reports_the_length_it_got():
    with pytest.raises(ValueError, match="Got 44 characters"):
        normalise_public_key(base64.b64encode(RAW).decode())


def test_sixty_four_non_hex_characters_are_named_as_non_hex():
    with pytest.raises(ValueError, match="non-hex character"):
        normalise_public_key("z" * 64)
