"""A digest that cannot be computed is a DENY, and recompute never matches on it (ops V3).

Mirrors tests/test_enforce_check.py in the server repo for the vendored core.
"""
import pytest

from moltrust_enforce import action_digest, enforce_check, recompute

PAY = {"verb": "transfer"}
MANDATE = {"mandate_version": "1.0", "grants": [{
    "action_binding": action_digest(PAY), "disposition": "allow",
    "type_fields": ["verb"], "constraints": []}]}

UNCOMPUTABLE = [("NaN", float("nan")), ("Infinity", float("inf")), ("lone surrogate", "\ud800")]


@pytest.mark.parametrize("label,bad", UNCOMPUTABLE, ids=[u[0] for u in UNCOMPUTABLE])
def test_uncomputable_transaction_is_deny_and_never_recomputes(label, bad):
    res = enforce_check(MANDATE, {"action": PAY, "memo": bad})
    assert res["verdict"] == "DENY"
    assert res["core"]["transaction_digest"] is None
    assert recompute(MANDATE, {"action": PAY, "memo": "\udfff"}, res) is False


def test_safe_transaction_still_permits():
    tx = {"action": PAY, "memo": 2**53 - 1}
    res = enforce_check(MANDATE, tx)
    assert res["verdict"] == "PERMIT" and recompute(MANDATE, tx, res) is True


def test_uncomputable_ancestor_is_deny_and_never_recomputes():
    parent1 = {**MANDATE, "note": float("nan")}
    parent2 = {**MANDATE, "note": "\udfff"}
    tx = {"action": PAY}
    res = enforce_check(MANDATE, tx, ancestors=[parent1])
    assert res["verdict"] == "DENY"
    assert recompute(MANDATE, tx, res, ancestors=[parent2]) is False
