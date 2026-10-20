"""A DID we did not issue is a question we cannot answer, not a malformed one.

`/identity/verify/` and `/skill/trust-score/` refused every foreign method with
400 — `did:web`, `did:key`, `did:base` and `did:pkh` all read back as *invalid
format* when their format was fine. That is the mirror image of the MoltGuard
defect fixed on the same day: there the validator was too loose, here it was
locked to one method.

The two files must agree on the grammar, because a caller that passes the gate
in MoltGuard and then gets a 400 here has been told two different things about
the same string. `moltguard/src/services/did.ts` is the other copy.
"""

import pytest
from fastapi import HTTPException

from app.main import (
    DID_FORM_HELP,
    MAX_DID_LENGTH,
    is_our_did,
    validate_did_any_method,
    validate_did_lookup,
)

FOREIGN = [
    "did:web:moltrust.ch",
    "did:web:api.moltrust.ch",
    "did:web:moltrust.ch:agents:42",
    "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
    "did:base:8453:0x3802cE7B2Ff8500D9dBFDE4dF69fE2C0F86238F5",
    "did:pkh:eip155:1:0xb9c5714089478a327f09197987f16f9e5d936e8a",
    "did:ethr:0xabc123",
]

MALFORMED = [
    "not-a-did",
    "ownify-e72e7337ea",          # the Ownify agent's own name
    "157224190be24072",           # our identifier without the prefix
    "0x3802cE7B2Ff8500D9dBFDE4dF69fE2C0F86238F5",  # a wallet
    "did:moltrust:",              # truncated — the commonest one in the log
    "did:moltrust",               # missing the second colon
    "did:",
    "did",
    "did:WEB:example.com",        # uppercase method
    "did:moltrust:abc:",          # trailing colon
    "did:x:a/b",                  # slash
    "../etc/passwd",
    "admin",
    "<script>",
]


@pytest.mark.parametrize("did", FOREIGN)
def test_a_foreign_method_is_accepted(did):
    assert validate_did_any_method(did) == did


@pytest.mark.parametrize("did", FOREIGN)
def test_a_foreign_method_is_not_ours(did):
    assert is_our_did(did) is False


def test_our_own_did_is_accepted_and_recognised():
    did = "did:moltrust:157224190be24072"
    assert validate_did_any_method(did) == did
    assert is_our_did(did) is True


@pytest.mark.parametrize("did", MALFORMED)
def test_a_malformed_did_is_still_a_400(did):
    with pytest.raises(HTTPException) as exc:
        validate_did_any_method(did)
    assert exc.value.status_code == 400


def test_the_message_names_the_form_rather_than_only_our_method():
    """The old text said `did:moltrust:<a-z0-9_-, 1-64 chars>`, which told
    someone holding a perfectly good did:web that their DID was malformed."""
    assert "did:<method>:<identifier>" in DID_FORM_HELP
    assert "did:web:example.com" in DID_FORM_HELP
    assert "wallet address is not a DID" in DID_FORM_HELP


def test_the_message_names_the_two_commonest_mistakes():
    """From 30 days of traffic: a bare identifier, and a wallet address."""
    assert "bare identifier" in DID_FORM_HELP
    assert "did:moltrust:" in DID_FORM_HELP


def test_length_is_bounded():
    with pytest.raises(HTTPException) as exc:
        validate_did_any_method("did:x:" + "a" * MAX_DID_LENGTH)
    assert exc.value.status_code == 400
    assert "longer than" in str(exc.value.detail)


def test_the_strict_lookup_validator_is_unchanged():
    """Twelve endpoints still use it, and they are about our own agents —
    credits, compliance, badges. Widening it there was not asked for and would
    let a did:web through to a balance lookup."""
    assert validate_did_lookup("did:moltrust:157224190be24072")
    for did in ("did:web:moltrust.ch", "did:key:z6Mkha"):
        with pytest.raises(HTTPException):
            validate_did_lookup(did)


def test_the_grammar_matches_the_moltguard_copy():
    """Both implement W3C DID Core §3.1. A caller that passes the MoltGuard
    gate and then gets a 400 here has been told two different things about the
    same string — so the same vectors must decide the same way on both sides."""
    same_verdict = {
        "did:moltrust:157224190be24072": True,
        "did:web:moltrust.ch": True,
        "did:web:moltrust.ch:agents:42": True,
        "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK": True,
        "did:pkh:eip155:1:0xb9c5714089478a327f09197987f16f9e5d936e8a": True,
        "did:x:a.b-c_d": True,
        "did:x:%20encoded": True,
        "did:web:": False,
        "did:moltrust:abc:": False,
        "did:Web:example.com": False,
        "notdid:web:x": False,
        "did:x:a/b": False,
        "": False,
    }
    for did, expected in same_verdict.items():
        try:
            validate_did_any_method(did)
            got = True
        except HTTPException:
            got = False
        assert got is expected, f"{did!r}: expected {expected}, got {got}"
