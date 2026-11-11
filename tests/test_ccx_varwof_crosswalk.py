"""varwof containment cross-walk vectors ccx-030..034 against assert_monotonic (ops CCX).

varwof/capability publishes 44 containment cross-walk vectors. Five of them,
profile "aae-constraint->clc-v1", map AAE delegation to a CLC grant pair and
assert whether the child is contained in the parent. This suite runs those five
through our Section 3 check, app/enforcement/delegation_chain.assert_monotonic,
and compares contained / not contained. The reason codes are not compared: our
errors are free text, and mapping them onto varwof's codes is out of scope.

Source, copied verbatim below and pinned:
  repo   https://github.com/varwof/capability
  commit 7b9c76cc41b31d93684f587693a0b09a62febed9
  file   data/_vectors/clc-d/containment-crosswalk-vectors.json
  sha256 ee5e7f03acf9a9a60b6440c117dfe0bc9bc4704d0e61ac40b4aa8a0b28cbbf6c

The profile form is {action, constraints: {name: bare value}}. Our check takes a
whole VC with constraints as {type, value[, currency]} and a validity block.
The vectors carry neither a currency nor a validity window, so this suite adds
both. They are ingredients of this test, not of the vectors, and they stay in
this file so that a reader sees every input that was not in the source.
"""

import pytest

from app.enforcement.delegation_chain import DelegationChainError, assert_monotonic

# --- The five vectors, verbatim from the pinned file (id, parent, child, expect). ---
VECTORS = [
    {"id": "ccx-030",
     "parent_source": {"action": "read", "constraints": {"max_transaction_value": 500}},
     "child_source": {"action": "read", "constraints": {"max_transaction_value": 300}},
     "expect": {"contains": True}},
    {"id": "ccx-031",
     "parent_source": {"action": "read", "constraints": {"max_transaction_value": 300}},
     "child_source": {"action": "read", "constraints": {"max_transaction_value": 500}},
     "expect": {"contains": False, "reason": "params_not_narrower"}},
    {"id": "ccx-032",
     "parent_source": {"action": "read", "constraints": {"max_transaction_value": 500}},
     "child_source": {"action": "book", "constraints": {"max_transaction_value": 500}},
     "expect": {"contains": False, "reason": "different_namespace"}},
    {"id": "ccx-033",
     "parent_source": {"action": "read",
                       "constraints": {"allowed_domains": ["booking.example.com",
                                                           "flights.example.com"]}},
     "child_source": {"action": "read",
                      "constraints": {"allowed_domains": ["booking.example.com"]}},
     "expect": {"contains": True}},
    {"id": "ccx-034",
     "parent_source": {"action": "read",
                       "constraints": {"allowed_domains": ["booking.example.com"]}},
     "child_source": {"action": "read",
                      "constraints": {"allowed_domains": ["booking.example.com",
                                                          "evil.example.com"]}},
     "expect": {"contains": False, "reason": "params_not_narrower"}},
]

# --- Ingredients: inputs this test adds because the vectors do not carry them. ---

# Ingredient 1, currency. AAE Section 3 requires max_transaction_value to carry a
# currency, and a delegated one must use the parent's. The profile drops it, so
# the same currency goes on both sides.
INGREDIENT_CURRENCY = "USD"
CURRENCY_VALUED = {"max_transaction_value"}

# Ingredient 2, validity window. The vectors declare validity out of scope; our
# check requires the block and compares it, so both sides get the same window and
# validity never decides a verdict here.
INGREDIENT_WINDOW = {"not_before": "2026-01-01T00:00:00Z",
                     "not_after": "2027-01-01T00:00:00Z"}

# Runs under added currency: the verdict of these vectors comes from comparing
# max_transaction_value, which our check only does with a currency present.
# ccx-032 also carries max_transaction_value and gets the currency too, but it is
# refused at the actions check, before any constraint is compared.
RUNS_UNDER_ADDED_CURRENCY = {"ccx-030", "ccx-031"}


def _vc(source: dict) -> dict:
    """Profile form -> the VC shape assert_monotonic reads, plus the ingredients."""
    constraints = []
    for ctype, value in source["constraints"].items():
        c = {"type": ctype, "value": value}
        if ctype in CURRENCY_VALUED:
            c["currency"] = INGREDIENT_CURRENCY
        constraints.append(c)
    return {"credentialSubject": {"aae": {
        "mandate": {"actions": [source["action"]]},
        "constraints": constraints,
        "validity": dict(INGREDIENT_WINDOW),
    }}}


def _contains(vector: dict) -> tuple[bool, str]:
    try:
        assert_monotonic(_vc(vector["child_source"]), _vc(vector["parent_source"]))
    except DelegationChainError as e:
        return False, str(e)
    return True, ""


def test_the_five_vectors_are_present():
    assert [v["id"] for v in VECTORS] == ["ccx-030", "ccx-031", "ccx-032", "ccx-033", "ccx-034"]


def test_currency_marker_names_currency_valued_vectors_only():
    for vid in RUNS_UNDER_ADDED_CURRENCY:
        v = next(v for v in VECTORS if v["id"] == vid)
        assert CURRENCY_VALUED & set(v["parent_source"]["constraints"]), vid


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_contains_matches_varwof(vector):
    got, why = _contains(vector)
    note = " (runs under added currency)" if vector["id"] in RUNS_UNDER_ADDED_CURRENCY else ""
    print(f'{vector["id"]} expect={vector["expect"]["contains"]} got={got}{note} {why}'.rstrip())
    assert got == vector["expect"]["contains"], why or "contained, expected not contained"
