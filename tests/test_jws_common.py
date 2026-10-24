"""Tests — shared JWS parsing guards (app/enforcement/jws_common.py).

The focus is `protected_header()`: the protected header has to be parsed with the same
duplicate-member rule the payload has had since the D-1 review. PyJWT's
`get_unverified_header()` resolves duplicates last-wins, which makes "the kid" a
statement about the parser rather than about the token; these tests pin the replacement.

No database and no network: every case here is a pure function over a string.
"""
import base64
import json

import jwt
import pytest

from app.enforcement.jws_common import (
    JwsGuardError,
    check_size_caps,
    protected_header,
    reject_duplicate_keys,
    split_kid,
)

KID = "did:moltrust:aaaabbbbccccdddd#key-1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jws(header_raw: bytes, payload: bytes = b'{"a":1}', sig: bytes = b"sig") -> str:
    """A compact JWS with a hand-built header segment. The signature is not checked here."""
    return f"{_b64(header_raw)}.{_b64(payload)}.{_b64(sig)}"


# ---------------- the positive path ----------------

def test_well_formed_header_parses():
    header = protected_header(_jws(json.dumps({"alg": "EdDSA", "kid": KID}).encode()))
    assert header == {"alg": "EdDSA", "kid": KID}


def test_agrees_with_pyjwt_where_there_is_no_duplicate():
    """The replacement must not change the answer for any header PyJWT reads correctly."""
    token = _jws(json.dumps({"alg": "EdDSA", "kid": KID, "cty": "aae+json"}).encode())
    assert protected_header(token) == jwt.get_unverified_header(token)


# ---------------- the reason this function exists ----------------

def test_duplicate_kid_is_rejected():
    token = _jws(b'{"alg":"EdDSA","kid":"did:moltrust:aaaabbbbccccdddd#key-1",'
                 b'"kid":"did:moltrust:eeeeffff00001111#key-1"}')
    with pytest.raises(JwsGuardError, match="duplicate JSON member: kid"):
        protected_header(token)


def test_pyjwt_would_have_taken_the_last_one():
    """Documents what is being prevented, so the test says why it is here."""
    token = _jws(b'{"alg":"EdDSA","kid":"did:x#1","kid":"did:y#1"}')
    assert jwt.get_unverified_header(token)["kid"] == "did:y#1"   # last-wins
    with pytest.raises(JwsGuardError):
        protected_header(token)


def test_duplicate_alg_is_rejected():
    with pytest.raises(JwsGuardError, match="duplicate JSON member: alg"):
        protected_header(_jws(b'{"alg":"EdDSA","alg":"none","kid":"did:x#1"}'))


def test_duplicate_in_a_nested_header_member_is_rejected():
    """A duplicate anywhere in the header, not only at the top level."""
    with pytest.raises(JwsGuardError, match="duplicate JSON member: kty"):
        protected_header(_jws(b'{"alg":"EdDSA","jwk":{"kty":"OKP","kty":"EC"}}'))


# ---------------- malformed input ----------------

@pytest.mark.parametrize("token, why", [
    ("not-a-jws", "no dots"),
    ("a.b", "one dot"),
    ("a.b.c.d", "three dots"),
])
def test_non_compact_input_is_rejected(token, why):
    with pytest.raises(JwsGuardError, match="compact JWS"):
        protected_header(token)


def test_non_string_input_is_rejected():
    with pytest.raises(JwsGuardError):
        protected_header(None)


def test_header_segment_outside_base64url_is_rejected():
    with pytest.raises(JwsGuardError, match="not base64url"):
        protected_header("head+er/=.payload.sig")


def test_header_that_is_not_json_is_rejected():
    with pytest.raises(JwsGuardError, match="not UTF-8 JSON"):
        protected_header(_jws(b"{not json"))


@pytest.mark.parametrize("raw", [b'"a string"', b"[1,2]", b"42", b"null"])
def test_header_that_is_not_an_object_is_rejected(raw):
    with pytest.raises(JwsGuardError, match="not a JSON object"):
        protected_header(_jws(raw))


def test_what_label_reaches_the_message():
    with pytest.raises(JwsGuardError, match="challenge_jws"):
        protected_header(_jws(b"{not json"), what="challenge_jws")


# ---------------- the guards that were already there ----------------

def test_payload_duplicates_still_rejected():
    with pytest.raises(JwsGuardError, match="duplicate JSON member: nonce"):
        json.loads(b'{"nonce":"a","nonce":"b"}', object_pairs_hook=reject_duplicate_keys)


def test_split_kid_still_strict():
    assert split_kid(KID) == ("did:moltrust:aaaabbbbccccdddd", "key-1")
    for bad in ("did:moltrust:a", "did:moltrust:a#", "#frag", "did:x/../y#1", "did:xé#1"):
        with pytest.raises(JwsGuardError):
            split_kid(bad)


def test_size_caps_still_bound_the_input():
    with pytest.raises(JwsGuardError, match="size limit"):
        check_size_caps(_jws(b'{"alg":"EdDSA"}', payload=b"x" * 20000))
