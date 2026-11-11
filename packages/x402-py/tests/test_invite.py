"""The invitation in the 403, against the vectors the TypeScript package runs.

Two kinds of probe throughout: one that the invite appears where it should,
and one that it stays away everywhere else. The second kind is the point. A
gate that attaches a referrer to the wrong denial credits registrations to
whoever happens to run that gate, and nobody downstream can tell.
"""
import json
import pathlib

import pytest

from moltrust_x402 import (
    CHALLENGE_URL,
    INVITE_SOURCE,
    INVITE_TEXT,
    REGISTER_URL,
    gate_block,
    invite_for,
    parse_operator_did,
)

OPERATOR = "did:moltrust:341e9f0e5a03b7df"

HERE = pathlib.Path(__file__).resolve().parent
VECTOR_FILE = HERE / "invite-vectors.json"
TS_VECTOR_FILE = (
    HERE.parent.parent / "x402" / "test" / "invite-vectors.json"
)


def _vectors():
    doc = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
    return doc["vectors"]


def test_enough_vectors_to_be_worth_something():
    assert len(_vectors()) >= 15


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["name"])
def test_shared_vectors(vector):
    got = gate_block(vector["decision"], parse_operator_did(vector["operator_did"]))
    assert got == vector["expected"]


def test_both_packages_read_the_same_file():
    # If the two drift, "both packages pass their vectors" stops meaning they
    # agree with each other.
    assert VECTOR_FILE.read_bytes() == TS_VECTOR_FILE.read_bytes()


def test_parse_operator_did_takes_a_well_formed_did_and_trims_it():
    assert parse_operator_did(OPERATOR) == OPERATOR
    assert parse_operator_did(f"  {OPERATOR}\n") == OPERATOR


@pytest.mark.parametrize("bad", [
    None, "", "   ", "did:moltrust:ZZZZ", "did:web:moltrust.ch",
    "did:moltrust:341E9F0E5A03B7DF", "did:moltrust:341e9f0e5a03b7d",
    "did:moltrust:341e9f0e5a03b7dff", OPERATOR + " and more",
])
def test_parse_operator_did_treats_anything_else_as_absent(bad):
    assert parse_operator_did(bad) is None


def test_attestation_missing_with_an_operator_did_gets_the_invite():
    assert invite_for({"reason": "attestation_missing"}, OPERATOR) == {
        "register_url": REGISTER_URL,
        "challenge_url": CHALLENGE_URL,
        "referrer_did": OPERATOR,
        "source": INVITE_SOURCE,
        "text": INVITE_TEXT,
    }


def test_no_operator_did_means_no_invite_and_no_fallback():
    assert invite_for({"reason": "attestation_missing"}, None) is None
    block = gate_block({"reason": "attestation_missing", "detail": "d"}, None)
    assert block == {"reason": "attestation_missing", "detail": "d"}
    assert "invite" not in block
    assert "referrer_did" not in json.dumps(block)


@pytest.mark.parametrize("reason", [
    "proof_missing", "attestation_invalid", "proof_invalid", "proof_replayed",
    "score_withheld", "track_record_invalid", "score_missing",
    "score_below_minimum", "credential_missing", "ok",
])
def test_no_other_denial_carries_an_invite(reason):
    assert invite_for({"reason": reason}, OPERATOR) is None


def test_a_denial_that_names_the_caller_carries_no_invite():
    decision = {
        "reason": "attestation_missing",
        "detail": "",
        "did": "did:moltrust:0000000000000001",
    }
    assert invite_for(decision, OPERATOR) is None


def test_the_source_is_the_package_not_the_moltguard_service():
    # The two have to be countable apart, or the cohort figures cannot say
    # whether third-party gates bring anyone in.
    assert INVITE_SOURCE == "x402-package-403"
    assert INVITE_SOURCE != "moltguard-402"


def test_a_real_decision_object_works_too():
    # The vectors use mappings; integrators hand in the gate's own Decision.
    from moltrust_enforce.gate import Decision

    block = gate_block(Decision(False, "attestation_missing", "send it"), OPERATOR)
    assert block["reason"] == "attestation_missing"
    assert block["detail"] == "send it"
    assert block["invite"]["referrer_did"] == OPERATOR

    allowed = gate_block(Decision(True, "ok", ""), OPERATOR)
    assert "invite" not in allowed
