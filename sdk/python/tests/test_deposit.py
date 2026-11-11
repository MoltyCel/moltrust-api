"""Tests — deposit constraints (`ch.moltrust.deposit.*`).

The cases live in `deposit-vectors.json` so that another implementation can replay them
without this package: mandate, planned transfer, history and evidence in; disposition and
reason out. Each of the five types has at least one `allow` and one `forbid` vector, the
execution window also `hold` vectors. The tests below the vector run pin what a vector file
cannot: the offset formula against a literal value, purity, and the evidence signatures.
"""
import copy
import hashlib
import json
import pathlib

import pytest
from conftest import evidence_item, source_id, trust_list

from moltrust_enforce import (
    DEPOSIT_TYPES, TOLERANCE_SECONDS, TransferDecision, build_bundle, check_transfer,
    deposit_constraint_problem, insurance_query, parse_deposit_constraints, period_key,
    rate_query, verify_deposit_evidence, window_offset_seconds,
)
from moltrust_enforce import deposit
from moltrust_enforce.evidence import parse_timestamp

VECTORS = json.loads((pathlib.Path(__file__).parent / "deposit-vectors.json")
                     .read_text(encoding="utf-8"))
CASES = VECTORS["cases"]


def _items(case):
    window = VECTORS["default_window"]
    items = []
    for n, e in enumerate(case.get("evidence", [])):
        items.append(evidence_item(e["source"], e["query"], e["value"],
                                   window=(e.get("valid_from", window["valid_from"]),
                                           e.get("valid_until", window["valid_until"])),
                                   nonce=f"n-{n}"))
    return items


def _bundle(case):
    spec = case.get("bundle", {})
    if spec.get("omit") or ("evidence" not in case and not spec):
        return None
    transfer = spec.get("bind_transfer", case["transfer"])
    stamp = spec.get("decision_timestamp", case["transfer"]["trigger_time"])
    return build_bundle(_items(case), case["mandate"], transfer, stamp)


def _run(case):
    return check_transfer(case["mandate"], case["transfer"], case["history"], _bundle(case))


# ------------------------------------------------------------------- vector run

@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_vector(case):
    decision = _run(case)
    expect = case["expect"]
    assert decision.disposition == expect["disposition"]
    assert decision.reason == expect["reason"]
    if "constraint_type" in expect:
        assert decision.constraint_type == expect["constraint_type"]


@pytest.mark.parametrize("ctype", DEPOSIT_TYPES)
def test_every_type_has_allow_and_forbid_vectors(ctype):
    mine = [c["expect"]["disposition"] for c in CASES if c["constraint"] == ctype]
    assert "allow" in mine and "forbid" in mine


def test_window_has_hold_vectors():
    held = [c for c in CASES if c["expect"]["disposition"] == "hold"]
    assert held and all(c["expect"]["constraint_type"] == deposit.EXECUTION_WINDOW_HOURS
                        for c in held)


def test_vector_file_states_the_tolerance():
    assert VECTORS["tolerance_seconds"] == TOLERANCE_SECONDS == 300


# ---------------------------------------------------------------------- offset

def test_offset_pinned_literal():
    """The derivation, frozen. If this moves, every recorded transfer stops recomputing."""
    assert window_offset_seconds("mandate-savings-0001", "rate-change-0042", 24) == 25937


@pytest.mark.parametrize("o", VECTORS["offsets"],
                         ids=[f"{o['trigger_id']}-{o['hours']}h" for o in VECTORS["offsets"]])
def test_offset_vectors(o):
    assert window_offset_seconds(o["mandate_id"], o["trigger_id"], o["hours"]) \
        == o["offset_seconds"]


def test_offset_is_the_sketch_formula():
    seed = hashlib.sha256("m-1|t-1".encode()).digest()
    assert window_offset_seconds("m-1", "t-1", 6) == int.from_bytes(seed[:8], "big") % 21600


def test_offset_differs_per_mandate_for_the_same_trigger():
    offsets = {window_offset_seconds(f"mandate-{n}", "same-trigger", 24) for n in range(50)}
    assert len(offsets) > 40
    assert all(0 <= x < 24 * 3600 for x in offsets)


@pytest.mark.parametrize("args", [("a|b", "t", 24), ("", "t", 24), ("m", "", 24),
                                  ("m", "t", 0), ("m", "t", 8761), ("m", "t", True),
                                  ("m", "t", 1.0)])
def test_offset_refuses_ambiguous_or_undefined_input(args):
    with pytest.raises(ValueError):
        window_offset_seconds(*args)


def test_hold_names_the_expected_time():
    case = next(c for c in CASES if c["id"] == "window-hold-at-trigger")
    decision = _run(case)
    assert decision.detail["offset_seconds"] == 25937
    assert decision.detail["expected_exec_time"] == "2026-10-15T15:12:17Z"
    assert decision.detail["tolerance_seconds"] == TOLERANCE_SECONDS


# ------------------------------------------------------------------- periods

@pytest.mark.parametrize("code,a,b,same", [
    ("DAIL", "2026-10-15T00:00:00Z", "2026-10-15T23:59:59Z", True),
    ("DAIL", "2026-10-15T23:59:59Z", "2026-10-16T00:00:00Z", False),
    ("WEEK", "2026-10-12T00:00:00Z", "2026-10-18T23:59:59Z", True),    # Mon..Sun
    ("WEEK", "2026-10-18T23:59:59Z", "2026-10-19T00:00:00Z", False),
    ("TOWK", "2026-10-05T00:00:00Z", "2026-10-18T23:59:59Z", True),
    ("TOWK", "2026-10-18T23:59:59Z", "2026-10-19T00:00:00Z", False),
    ("TWMN", "2026-10-15T23:59:59Z", "2026-10-16T00:00:00Z", False),
    ("TWMN", "2026-10-16T00:00:00Z", "2026-10-31T23:59:59Z", True),
    ("MNTH", "2026-10-01T00:00:00Z", "2026-10-31T23:59:59Z", True),
    ("TOMN", "2026-09-01T00:00:00Z", "2026-10-31T23:59:59Z", True),
    ("TOMN", "2026-10-31T23:59:59Z", "2026-11-01T00:00:00Z", False),
    ("QUTR", "2026-09-30T23:59:59Z", "2026-10-01T00:00:00Z", False),
    ("FOMN", "2026-09-01T00:00:00Z", "2026-12-31T23:59:59Z", True),
    ("FOMN", "2026-08-31T23:59:59Z", "2026-09-01T00:00:00Z", False),
    ("SEMI", "2026-07-01T00:00:00Z", "2026-12-31T23:59:59Z", True),
    ("YEAR", "2026-12-31T23:59:59Z", "2027-01-01T00:00:00Z", False),
    ("TYEA", "2026-01-01T00:00:00Z", "2027-12-31T23:59:59Z", True),
    ("TYEA", "2027-12-31T23:59:59Z", "2028-01-01T00:00:00Z", False),
])
def test_period_boundaries(code, a, b, same):
    assert (period_key(code, parse_timestamp(a)) == period_key(code, parse_timestamp(b))) is same


def test_period_codes_cover_vi_4_6_except_intraday():
    vi_4_6 = {"INDA", "DAIL", "WEEK", "TOWK", "TWMN", "MNTH", "TOMN", "QUTR", "FOMN",
              "SEMI", "YEAR", "TYEA"}
    assert set(deposit.PERIOD_CODES) | set(deposit.REFUSED_PERIOD_CODES) == vi_4_6
    assert deposit.REFUSED_PERIOD_CODES == ("INDA",)


# --------------------------------------------------------------- parsing (VI §3)

def test_parsing_preserves_the_constraint_objects():
    case = next(c for c in CASES if c["id"] == "struct-allow-unknown-field-preserved")
    before = copy.deepcopy(case["mandate"])
    picked, problem = parse_deposit_constraints(case["mandate"]["constraints"])
    assert problem is None
    assert picked[0][1] is case["mandate"]["constraints"][0]
    _run(case)
    assert case["mandate"] == before


def test_parsing_leaves_vi_types_to_the_vi_verifier():
    case = next(c for c in CASES if c["id"] == "full-allow")
    picked, problem = parse_deposit_constraints(case["mandate"]["constraints"])
    assert problem is None
    assert [c["type"] for _, c in picked] == list(DEPOSIT_TYPES)
    assert [i for i, _ in picked] == [2, 3, 4, 5, 6]


@pytest.mark.parametrize("c", [
    None, [], {"type": ""}, {"days": 1},
    {"type": "ch.moltrust.deposit.min_hold_days", "days": True},
    {"type": "ch.moltrust.deposit.min_hold_days", "days": 10 ** 16},
    {"type": "ch.moltrust.deposit.sweep_threshold_bp", "min_rate_diff_bp": -1, "rate_basis": "APY"},
    {"type": "ch.moltrust.deposit.sweep_threshold_bp", "min_rate_diff_bp": 50},
    {"type": "ch.moltrust.deposit.max_sweeps_per_period", "max": 2},
    {"type": "ch.moltrust.deposit.insured_counterparty_only", "schemes": ["US-FDIC", "US-FDIC"],
     "max_insured_balance": {"currency": "USD", "amount": 1}},
    {"type": "ch.moltrust.deposit.insured_counterparty_only", "schemes": ["US-FDIC"],
     "max_insured_balance": {"currency": "usd", "amount": 1}},
    {"type": "ch.moltrust.deposit.insured_counterparty_only", "schemes": ["US-FDIC"],
     "max_insured_balance": {"currency": "USD", "amount": -1}},
    {"type": "ch.moltrust.deposit.insured_counterparty_only", "schemes": "US-FDIC",
     "max_insured_balance": {"currency": "USD", "amount": 1}},
    {"type": "ch.moltrust.deposit.execution_window_hours", "hours": -24},
])
def test_malformed_constraints_have_a_problem(c):
    assert deposit_constraint_problem(c) is not None


# -------------------------------------------------------- fail-closed inputs

BASE = next(c for c in CASES if c["id"] == "full-allow")


@pytest.mark.parametrize("mutate", [
    lambda m, t, h: (None, t, h),
    lambda m, t, h: ({**m, "constraints": "x"}, t, h),
    lambda m, t, h: (m, {**t, "mandate_id": "other"}, h),
    lambda m, t, h: (m, {**t, "target": t["source"]}, h),
    lambda m, t, h: (m, {**t, "exec_time": "2026-10-15T07:59:59Z"}, h),
    lambda m, t, h: (m, {**t, "exec_time": "2026-10-15T15:12:17+00:00"}, h),
    lambda m, t, h: (m, {**t, "amount": {"currency": "USD", "amount": 1.5}}, h),
    lambda m, t, h: (m, t, None),
    lambda m, t, h: (m, t, {"transfers": "x"}),
    lambda m, t, h: (m, t, {"transfers": [{"source": "a"}]}),
])
def test_malformed_input_is_forbid_not_an_exception(mutate):
    m, t, h = mutate(copy.deepcopy(BASE["mandate"]), copy.deepcopy(BASE["transfer"]),
                     copy.deepcopy(BASE["history"]))
    decision = check_transfer(m, t, h, _bundle(BASE))
    assert decision.disposition == "forbid"
    assert decision.reason


def test_a_passed_bundle_is_checked_even_when_no_constraint_needs_it():
    case = next(c for c in CASES if c["id"] == "hold-allow-elapsed")
    assert check_transfer(case["mandate"], case["transfer"], case["history"]).disposition == "allow"
    broken = check_transfer(case["mandate"], case["transfer"], case["history"], {"items": []})
    assert broken.disposition == "forbid"


def test_bundle_bound_to_another_mandate_is_forbid():
    other = copy.deepcopy(BASE["mandate"])
    other["mandate_id"] = "mandate-savings-0002"
    bundle = build_bundle(_items(BASE), other, BASE["transfer"], BASE["transfer"]["trigger_time"])
    decision = check_transfer(BASE["mandate"], BASE["transfer"], BASE["history"], bundle)
    assert (decision.disposition, decision.reason) == \
        ("forbid", "bundle mandate_ref does not bind this mandate")


# ----------------------------------------------------------------- purity

def test_same_inputs_same_decision_and_inputs_untouched():
    args = (BASE["mandate"], BASE["transfer"], BASE["history"], _bundle(BASE))
    snapshot = copy.deepcopy(args)
    first, second = check_transfer(*args), check_transfer(*args)
    assert first == second
    assert args == snapshot


def test_kernel_reads_no_clock_and_opens_no_socket(monkeypatch):
    import socket
    import time

    def boom(*_a, **_k):
        raise AssertionError("the kernel must not touch this")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(time, "time", boom)
    for case in CASES:
        _run(case)


def test_decision_unpacks_like_the_sketch():
    disposition, reason = _run(BASE)
    assert (disposition, reason) == ("allow", None)
    assert isinstance(_run(BASE), TransferDecision)


# --------------------------------------------------------- evidence authenticity

def test_queries_match_the_vector_queries():
    case = next(c for c in CASES if c["id"] == "full-allow")
    queries = [e["query"] for e in case["evidence"]]
    assert rate_query("bank-a", "APY") in queries
    assert insurance_query("bank-b") in queries


def test_evidence_signed_by_trusted_sources_verifies():
    bundle = _bundle(BASE)
    assert verify_deposit_evidence(bundle, trust_list("deposit-rates", "deposit-insurance")) is None


def test_evidence_from_an_untrusted_source_fails():
    bundle = _bundle(BASE)
    problem = verify_deposit_evidence(bundle, trust_list("deposit-rates"))
    assert problem is not None and source_id("deposit-insurance") in problem


def test_forged_rate_fails_signature_check_but_kernel_does_not_verify_signatures():
    """The kernel checks structure, binding and windows; V2 checks who signed. A rate
    signed by the insurance source passes the kernel and fails `verify_deposit_evidence`."""
    t, m = BASE["transfer"], BASE["mandate"]
    window = (VECTORS["default_window"]["valid_from"], VECTORS["default_window"]["valid_until"])
    items = [evidence_item("deposit-rates", rate_query("bank-a", "APY"), 10, window, "n-0"),
             evidence_item("deposit-rates", rate_query("bank-b", "APY"), 350, window, "n-1",
                           sign_with="deposit-insurance"),
             evidence_item("deposit-insurance", insurance_query("bank-b"), "US-FDIC", window, "n-2")]
    bundle = build_bundle(items, m, t, t["trigger_time"])
    assert check_transfer(m, t, BASE["history"], bundle).disposition == "allow"
    assert verify_deposit_evidence(bundle, trust_list("deposit-rates", "deposit-insurance"))


def test_a_stale_item_fails_v3_as_well():
    case = next(c for c in CASES if c["id"] == "sweep-forbid-stale-rate")
    problem = verify_deposit_evidence(_bundle(case), trust_list("deposit-rates"))
    assert problem is not None and "V3" in problem
