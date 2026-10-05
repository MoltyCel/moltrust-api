"""Tests — grant attenuation per delegation hop (AAE -02 §5 step 9).

Two entry points share one rule: `enforce_check(..., ancestors=[...])` records each hop as a
`grant_attenuation` predicate in the recomputable core, and the chain walk of the acceptance
gate (`delegation_chain.assert_monotonic`) rejects a widening hop. All pure — no database,
no app. The walk is driven with a stub `verify_core`, which is the injection point the gate
uses as well.
"""
import asyncio
import json

import pytest

from app.enforcement import delegation_chain as dc
from app.enforcement.delegation_chain import DelegationChainError, verify_delegation_chain
from app.enforcement.enforce_check import (
    DENY, MAX_ANCESTORS, PENDING, PERMIT, _TAG_MANDATE, _digest, action_digest,
    enforce_check, grant_attenuation_problem, recompute,
)

PAY = {"verb": "transfer", "asset": "USDC", "chain": "base"}
WITHDRAW = {"verb": "withdraw", "asset": "USDC", "chain": "base"}
ADDR = "0xABCDEF0123456789ABCDEF0123456789ABCDEF01"

RANGE_1000 = {"type": "range", "field": "amount", "lo": 0, "hi": 1000}
RANGE_100 = {"type": "range", "field": "amount", "lo": 0, "hi": 100}
RANGE_5000 = {"type": "range", "field": "amount", "lo": 0, "hi": 5000}
TO = {"type": "exact", "field": "to", "value": ADDR}
PURPOSE_TRAVEL = {"type": "enum", "field": "purpose_code", "values": ["travel.booking"]}
PURPOSE_WIDE = {"type": "enum", "field": "purpose_code",
                "values": ["travel.booking", "finance.payment"]}


def _grant(disposition="allow", constraints=(), action=PAY):
    return {"action_binding": action_digest(action), "type_fields": list(action),
            "disposition": disposition, "constraints": list(constraints)}


def _mandate(*grants):
    return {"mandate_version": "1.0", "grants": list(grants)}


def _tx(**over):
    t = {"action": dict(PAY), "to": ADDR, "amount": 50, "purpose_code": "travel.booking"}
    t.update(over)
    return t


def _att(res):
    return [p for p in res["trace"] if p["predicate"] == "grant_attenuation"]


# ------------------------------------------------------------- the rule on its own

def test_equal_child_is_not_broader():
    m = _mandate(_grant("allow", [TO, RANGE_1000]))
    assert grant_attenuation_problem(m, m) is None


def test_narrower_range_passes_and_wider_range_fails():
    parent = _mandate(_grant("allow", [RANGE_1000]))
    assert grant_attenuation_problem(_mandate(_grant("allow", [RANGE_100])), parent) is None
    problem = grant_attenuation_problem(_mandate(_grant("allow", [RANGE_5000])), parent)
    assert problem and "amount" in problem


def test_enum_subset_passes_and_superset_fails():
    parent = _mandate(_grant("allow", [PURPOSE_TRAVEL]))
    assert grant_attenuation_problem(_mandate(_grant("allow", [PURPOSE_TRAVEL])), parent) is None
    assert grant_attenuation_problem(_mandate(_grant("allow", [PURPOSE_WIDE])), parent)


def test_exact_inside_an_enum_is_narrower():
    parent = _mandate(_grant("allow", [PURPOSE_WIDE]))
    child = _mandate(_grant("allow", [{"type": "exact", "field": "purpose_code",
                                       "value": "finance.payment"}]))
    assert grant_attenuation_problem(child, parent) is None


def test_differing_exact_is_rejected():
    parent = _mandate(_grant("allow", [TO]))
    child = _mandate(_grant("allow", [{"type": "exact", "field": "to", "value": "0x01"}]))
    assert grant_attenuation_problem(child, parent)


def test_a_parent_constraint_absent_from_the_child_is_rejected():
    parent = _mandate(_grant("allow", [TO, RANGE_1000]))
    problem = grant_attenuation_problem(_mandate(_grant("allow", [RANGE_100])), parent)
    assert problem and "'to'" in problem


def test_constraint_on_another_field_does_not_count():
    parent = _mandate(_grant("allow", [RANGE_1000]))
    other = {"type": "range", "field": "fee", "lo": 0, "hi": 10}
    assert grant_attenuation_problem(_mandate(_grant("allow", [other])), parent)


def test_an_added_child_constraint_only_narrows():
    parent = _mandate(_grant("allow", [RANGE_1000]))
    assert grant_attenuation_problem(_mandate(_grant("allow", [RANGE_1000, TO])), parent) is None


def test_child_grant_for_an_action_the_parent_does_not_bind_is_rejected():
    parent = _mandate(_grant("allow", [RANGE_1000]))
    child = _mandate(_grant("allow", [RANGE_100]), _grant("allow", [], action=WITHDRAW))
    problem = grant_attenuation_problem(child, parent)
    assert problem and "grant[1]" in problem and "no parent grant" in problem


@pytest.mark.parametrize("parent_disp,child_disp,ok", [
    ("allow", "allow", True), ("allow", "hold", True), ("allow", "forbid", True),
    ("hold", "hold", True), ("hold", "forbid", True),
    ("hold", "allow", False), ("forbid", "allow", False), ("forbid", "hold", False),
    ("forbid", "forbid", True),
])
def test_disposition_never_moves_down(parent_disp, child_disp, ok):
    parent = _mandate(_grant(parent_disp, [RANGE_1000]))
    child = _mandate(_grant(child_disp, [RANGE_100]))
    assert (grant_attenuation_problem(child, parent) is None) is ok


def test_dropping_a_parent_forbid_is_rejected():
    # Each child grant is "covered" by the parent's allow, yet the parent denies the action
    # outright: forbid outranks every grant for the same action (§2.2.3 step 5).
    parent = _mandate(_grant("allow", [RANGE_1000]), _grant("forbid"))
    child = _mandate(_grant("allow", [RANGE_100]))
    assert enforce_check(parent, _tx())["verdict"] == DENY
    problem = grant_attenuation_problem(child, parent)
    assert problem and "forbids" in problem


def test_a_child_forbid_needs_no_constraints():
    parent = _mandate(_grant("allow", [TO, RANGE_1000]))
    assert grant_attenuation_problem(_mandate(_grant("forbid")), parent) is None


def test_an_earlier_parent_hold_on_the_same_transactions_blocks_an_allow():
    # The parent stops at its first grant whose constraints hold: amount 50 is PENDING there.
    parent = _mandate(_grant("hold", [RANGE_1000]), _grant("allow", [RANGE_100]))
    child = _mandate(_grant("allow", [RANGE_100]))
    assert enforce_check(parent, _tx())["verdict"] == PENDING
    problem = grant_attenuation_problem(child, parent)
    assert problem and "holds first" in problem


def test_a_disjoint_earlier_hold_does_not_block():
    parent = _mandate(_grant("hold", [{"type": "range", "field": "amount", "lo": 101, "hi": 1000}]),
                      _grant("allow", [RANGE_100]))
    assert grant_attenuation_problem(_mandate(_grant("allow", [RANGE_100])), parent) is None


def test_a_child_grant_that_can_never_hold_does_not_widen():
    parent = _mandate(_grant("allow", [RANGE_100]))
    dead = {"type": "range", "field": "amount", "lo": 10, "hi": 1}
    assert grant_attenuation_problem(_mandate(_grant("allow", [dead])), parent) is None


def test_a_malformed_mandate_on_either_side_is_a_problem():
    good = _mandate(_grant("allow", [RANGE_100]))
    assert grant_attenuation_problem({"grants": []}, good).startswith("child ")
    assert grant_attenuation_problem(good, "nope").startswith("parent ")


# ------------------------------------------------- in the recomputable verdict core

def test_no_ancestors_leaves_the_core_unchanged():
    m = _mandate(_grant("allow", [TO, RANGE_1000]))
    base = enforce_check(m, _tx())
    assert enforce_check(m, _tx(), ancestors=None)["core_digest"] == base["core_digest"]
    assert enforce_check(m, _tx(), ancestors=[])["core_digest"] == base["core_digest"]
    assert not _att(base)


def test_narrowed_hop_permits_and_records_the_hop():
    root = _mandate(_grant("allow", [RANGE_1000]))
    child = _mandate(_grant("allow", [RANGE_100]))
    res = enforce_check(child, _tx(), ancestors=[root])
    assert res["verdict"] == PERMIT
    (hop,) = _att(res)
    assert hop["result"] == "PASS" and hop["field"] == "ancestors[0]"
    assert hop["value"] == _digest(_TAG_MANDATE, child)
    assert hop["bound"] == _digest(_TAG_MANDATE, root)
    preds = [p["predicate"] for p in res["core"]["trace"]]
    assert preds.index("grant_attenuation") < preds.index("type_fields")


def test_widened_hop_denies_before_any_grant_is_evaluated():
    root = _mandate(_grant("allow", [RANGE_1000]))
    child = _mandate(_grant("allow", [RANGE_5000]))
    res = enforce_check(child, _tx(), ancestors=[root])
    assert res["verdict"] == DENY and res["grant_index"] is None
    assert [p["predicate"] for p in res["trace"]] == ["mandate_present", "grant_attenuation"]
    assert _att(res)[0]["result"] == "FAIL"
    assert "hop 0" in res["reason"]


def test_purpose_as_enum_grant_widened_denies_although_the_transaction_fits_the_root():
    root = _mandate(_grant("allow", [PURPOSE_TRAVEL, RANGE_1000]))
    child = _mandate(_grant("allow", [PURPOSE_WIDE, RANGE_1000]))
    assert enforce_check(root, _tx())["verdict"] == PERMIT
    res = enforce_check(child, _tx(), ancestors=[root])
    assert res["verdict"] == DENY
    assert "purpose_code" in res["reason"]


def test_second_hop_widening_is_named_and_the_first_hop_passes():
    root = _mandate(_grant("allow", [RANGE_1000]))
    mid = _mandate(_grant("allow", [RANGE_100]))
    leaf = _mandate(_grant("allow", [RANGE_1000]))   # back up to the root's bound
    res = enforce_check(leaf, _tx(), ancestors=[root, mid])
    assert [(p["field"], p["result"]) for p in _att(res)] == [
        ("ancestors[0]", "PASS"), ("ancestors[1]", "FAIL")]
    assert res["verdict"] == DENY


@pytest.mark.parametrize("ancestors", ["root", {"grants": []}, [None],
                                       [_mandate(_grant())] * (MAX_ANCESTORS + 1)])
def test_unusable_ancestors_deny(ancestors):
    res = enforce_check(_mandate(_grant()), _tx(), ancestors=ancestors)
    assert res["verdict"] == DENY
    assert _att(res)[-1]["result"] == "FAIL"


def test_recompute_needs_the_same_ancestors():
    root = _mandate(_grant("allow", [RANGE_1000]))
    child = _mandate(_grant("allow", [RANGE_100]))
    rec = enforce_check(child, _tx(), ancestors=[root])
    assert recompute(child, _tx(), rec, ancestors=[root])
    assert not recompute(child, _tx(), rec)
    other_root = _mandate(_grant("allow", [RANGE_5000]))
    assert not recompute(child, _tx(), rec, ancestors=[other_root])


def test_core_is_independent_of_key_order():
    def rev(o):
        if isinstance(o, dict):
            return {k: rev(o[k]) for k in reversed(list(o))}
        return [rev(v) for v in o] if isinstance(o, list) else o
    root = _mandate(_grant("allow", [PURPOSE_WIDE, RANGE_1000]))
    child = _mandate(_grant("allow", [PURPOSE_TRAVEL, RANGE_100]))
    a = enforce_check(child, _tx(), ancestors=[root])
    b = enforce_check(rev(child), rev(_tx()), ancestors=[rev(root)])
    assert a["core_digest"] == b["core_digest"]


# ------------------------------------------------------ the chain walk (VC level)

WIDE = {"not_before": "2026-01-01T00:00:00Z", "not_after": "2028-01-01T00:00:00Z"}


def _vc(vc_id, issuer, subject, mandate, delegation=None):
    m = dict(mandate)
    if delegation:
        m["delegation"] = delegation
    return {"id": vc_id, "issuer": issuer,
            "credentialSubject": {"id": subject, "aae": {
                "mandate": m, "constraints": [], "validity": dict(WIDE)}}}


def _pair(parent_mandate, child_mandate):
    parent = _vc("urn:uuid:p", "did:example:a", "did:example:a",
                 {"actions": ["transfer"], "delegation_policy": {"max_depth": 2},
                  **parent_mandate})
    child = _vc("urn:uuid:c", "did:example:a", "did:example:b",
                {"actions": ["transfer"], **child_mandate},
                delegation={"delegator_did": "did:example:a", "delegator_aae_id": "urn:uuid:p",
                            "depth": 1, "max_depth": 2})
    return child, parent


def test_assert_monotonic_without_grants_is_unchanged():
    child, parent = _pair({}, {})
    dc.assert_monotonic(child, parent)


def test_assert_monotonic_accepts_narrowed_grants():
    child, parent = _pair({"grants": [_grant("allow", [RANGE_1000])]},
                          {"grants": [_grant("allow", [RANGE_100])]})
    dc.assert_monotonic(child, parent)


def test_assert_monotonic_rejects_widened_grants():
    child, parent = _pair({"grants": [_grant("allow", [RANGE_100])]},
                          {"grants": [_grant("allow", [RANGE_1000])]})
    with pytest.raises(DelegationChainError, match="grant attenuation"):
        dc.assert_monotonic(child, parent)


def test_assert_monotonic_rejects_a_child_that_sheds_the_parent_grants():
    child, parent = _pair({"grants": [_grant("allow", [RANGE_100])]}, {})
    with pytest.raises(DelegationChainError, match="parent carries grants"):
        dc.assert_monotonic(child, parent)


def test_assert_monotonic_rejects_child_grants_under_an_actions_only_parent():
    child, parent = _pair({}, {"grants": [_grant("allow", [RANGE_100])]})
    with pytest.raises(DelegationChainError, match="none to cover"):
        dc.assert_monotonic(child, parent)


def test_the_walk_rejects_a_widening_hop():
    child, parent = _pair({"grants": [_grant("allow", [RANGE_100])]},
                          {"grants": [_grant("allow", [RANGE_1000])]})
    by_jws = {"parent-jws": (parent, "did:example:a")}

    async def stub_core(raw, _conn):
        return by_jws[raw]

    async def run():
        return await verify_delegation_chain(
            child, aae_jws="child-jws", ancestor_jws=["parent-jws"], conn=None,
            verify_core=stub_core, signing_did="did:example:a")

    with pytest.raises(DelegationChainError, match="grant attenuation"):
        asyncio.run(run())

    # Same chain, narrowed: the walk completes.
    child2, _ = _pair({"grants": [_grant("allow", [RANGE_1000])]},
                      {"grants": [_grant("allow", [RANGE_100])]})

    async def run2():
        return await verify_delegation_chain(
            child2, aae_jws="child-jws", ancestor_jws=["parent-jws"], conn=None,
            verify_core=stub_core, signing_did="did:example:a")

    by_jws["parent-jws"] = (_pair({"grants": [_grant("allow", [RANGE_1000])]}, {})[1],
                            "did:example:a")
    assert asyncio.run(run2())["chain_length"] == 1


def test_mandate_json_roundtrip_is_stable():
    # Ancestors arrive as JSON over the wire; the digest must not depend on Python types.
    root = _mandate(_grant("allow", [RANGE_1000]))
    child = _mandate(_grant("allow", [RANGE_100]))
    a = enforce_check(child, _tx(), ancestors=[root])
    b = enforce_check(json.loads(json.dumps(child)), _tx(),
                      ancestors=json.loads(json.dumps([root])))
    assert a["core_digest"] == b["core_digest"]
