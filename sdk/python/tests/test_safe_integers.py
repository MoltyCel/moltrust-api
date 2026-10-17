"""moltrust-enforce 0.7.1: no digest over a rounded integer (ops V3).

jcs 0.2.1 writes every number through a double, so on 0.7.0 two inputs that
differed only in an integer beyond 2**53-1 shared one digest. Each public digest
function now refuses such an integer (None), and the check answers DENY.
"""
import pytest

from moltrust_enforce import action_digest, core_digest, enforce_check, recompute
from moltrust_enforce import evidence
from moltrust_enforce._jcs import MAX_SAFE_INTEGER, canonicalize

PAY = {"verb": "transfer"}
MANDATE = {"mandate_version": "1.0", "grants": [{
    "action_binding": action_digest(PAY), "disposition": "allow",
    "type_fields": ["verb"], "constraints": []}]}

# Pairs jcs 0.2.1 writes as the same bytes.
NEIGHBOURS = [(2**53, 2**53 + 1), (-(2**53), -(2**53 + 1)), (10**21, 10**21 + 1)]


def test_bound_is_i_json():
    assert MAX_SAFE_INTEGER == 2**53 - 1


@pytest.mark.parametrize("a,b", NEIGHBOURS)
def test_canonicalize_refuses_instead_of_rounding(a, b):
    with pytest.raises(ValueError):
        canonicalize({"n": b})


@pytest.mark.parametrize("a,b", NEIGHBOURS)
def test_action_digest_never_collides(a, b):
    assert action_digest({**PAY, "ref": b}) is None


@pytest.mark.parametrize("a,b", NEIGHBOURS)
def test_core_digest_never_collides(a, b):
    assert core_digest({"x": b}) is None


@pytest.mark.parametrize("a,b", NEIGHBOURS)
def test_enforce_check_denies_and_recompute_refuses(a, b):
    res = enforce_check(MANDATE, {"action": PAY, "amount_minor": b})
    assert res["verdict"] == "DENY"
    assert recompute(MANDATE, {"action": PAY, "amount_minor": a}, res) is False


@pytest.mark.parametrize("a,b", NEIGHBOURS)
def test_evidence_digests_never_collide(a, b):
    assert evidence.query_key({"q": b}) is None
    assert evidence.evidence_payload_bytes({"v": b}) is None


def test_bound_still_canonicalizes_unchanged():
    for n in (2**53 - 1, -(2**53 - 1), 0):
        assert canonicalize({"n": n}) == f'{{"n":{n}}}'.encode()
    res = enforce_check(MANDATE, {"action": PAY, "amount_minor": 2**53 - 1})
    assert res["verdict"] == "PERMIT"
