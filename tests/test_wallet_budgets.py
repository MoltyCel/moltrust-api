"""Two budgets share one wallet, and they are never netted against each other.

`0xd8f5` carries the original test/bounty budget (cap 21, closed for bounties
with 0.75 left) and, since 2026-09-21, the defect bounty (cap 30, 10 a month,
remainder expires). One balance, two ceilings — so every outflow has to be
attributed before either number means anything.
"""
import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "wallet_reconcile", os.path.join(REPO, "scripts", "wallet_reconcile.py"))
wr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wr)

MONTH = "2026-10"


def flow(tx, usdc, ts=f"{MONTH}-05T12:00:00"):
    return {"tx": tx, "usdc": usdc, "ts": ts, "internal": False}


def booking(tx, purpose):
    return {"tx": tx, "purpose": purpose}


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

def test_a_defect_bounty_purpose_lands_in_the_defect_pot():
    assert wr.budget_of("defect-bounty 2026-10") == wr.DEFECT_BOUNTY_PREFIX


def test_case_does_not_matter():
    assert wr.budget_of("DEFECT-BOUNTY 2026-11") == wr.DEFECT_BOUNTY_PREFIX
    assert wr.budget_of("  Defect-Bounty 2026-12  ") == wr.DEFECT_BOUNTY_PREFIX


def test_the_german_bounty_round_purposes_stay_in_the_old_budget():
    """The trap this match is narrow for. Bounty round 1 booked 78 payouts on
    2026-09-21 with purposes like `Defekt-Bonus Bounty-Runde 1 (SUB-…)`. A
    looser match on "defekt" or "bonus" would move 5.25 USDC of already-spent
    money into a pot that has not paid out a cent."""
    for purpose in ("Defekt-Bonus Bounty-Runde 1 (SUB-PXDA3ZTH) -> 0x2E8dFc",
                    "Anerkennung Bounty-Runde 1 (SUB-QBMJA7AC) -> 0xf93A8a8",
                    "Defekt-Bounty-Vorbereitung",
                    "bonus defect"):
        assert wr.budget_of(purpose) == "test/bounty-r1", purpose


def test_an_unknown_purpose_falls_to_the_closed_budget():
    assert wr.budget_of("x402 self-payment") == "test/bounty-r1"
    assert wr.budget_of("") == "test/bounty-r1"
    assert wr.budget_of(None) == "test/bounty-r1"


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------

def test_each_budget_counts_only_its_own_spending():
    out = wr.split_by_budget(
        [flow("0xa", 3.0), flow("0xb", 0.5)],
        [booking("0xa", "defect-bounty 2026-10"), booking("0xb", "Anerkennung Bounty-Runde 1")],
        MONTH,
    )
    assert out[wr.DEFECT_BOUNTY_PREFIX]["spent"] == 3.0
    assert out["test/bounty-r1"]["spent"] == 0.5
    assert out[wr.DEFECT_BOUNTY_PREFIX]["remaining"] == 27.0
    assert out["test/bounty-r1"]["remaining"] == 20.5


def test_the_two_budgets_are_never_netted():
    """Spending the defect pot must not make the closed one look roomier, and
    the other way round. They are separate ceilings on one balance."""
    out = wr.split_by_budget([flow("0xa", 10.0)],
                             [booking("0xa", "defect-bounty 2026-10")], MONTH)
    assert out["test/bounty-r1"]["spent"] == 0.0
    assert out["test/bounty-r1"]["remaining"] == 21.0


def test_an_unbooked_outflow_is_charged_to_the_closed_budget():
    """No booking means no purpose. Guessing it into the roomier pot would
    soften the one number that must not be soft, and an unattributed spend is
    already the alarm."""
    out = wr.split_by_budget([flow("0xmystery", 2.0)], [], MONTH)
    assert out["test/bounty-r1"]["spent"] == 2.0
    assert out[wr.DEFECT_BOUNTY_PREFIX]["spent"] == 0.0
    assert out["_unattributed"] == 2.0


# ---------------------------------------------------------------------------
# The month
# ---------------------------------------------------------------------------

def test_the_monthly_allowance_counts_only_this_month():
    out = wr.split_by_budget(
        [flow("0xa", 4.0, "2026-10-02T09:00:00"),
         flow("0xb", 6.0, "2026-09-30T23:59:00")],
        [booking("0xa", "defect-bounty 2026-10"),
         booking("0xb", "defect-bounty 2026-09")],
        "2026-10",
    )
    d = out[wr.DEFECT_BOUNTY_PREFIX]
    assert d["spent"] == 10.0, "the cumulative cap sees both"
    assert d["month_spent"] == 4.0, "the month sees only October"
    assert d["month_remaining"] == 6.0


def test_last_months_remainder_does_not_carry():
    """3 USDC paid in October leaves 10 in November, not 17."""
    october = wr.split_by_budget([flow("0xa", 3.0, "2026-10-10T12:00:00")],
                                 [booking("0xa", "defect-bounty 2026-10")], "2026-10")
    november = wr.split_by_budget([flow("0xa", 3.0, "2026-10-10T12:00:00")],
                                  [booking("0xa", "defect-bounty 2026-10")], "2026-11")
    assert october[wr.DEFECT_BOUNTY_PREFIX]["month_remaining"] == 7.0
    assert november[wr.DEFECT_BOUNTY_PREFIX]["month_remaining"] == 10.0


def test_the_closed_budget_has_no_monthly_allowance():
    out = wr.split_by_budget([], [], MONTH)
    assert "monthly" not in out["test/bounty-r1"]
    assert out[wr.DEFECT_BOUNTY_PREFIX]["monthly"] == 10.0


# ---------------------------------------------------------------------------
# The caps themselves
# ---------------------------------------------------------------------------

def test_the_caps_are_what_lars_set():
    assert wr.BUDGETS["test/bounty-r1"]["cap"] == 21.0
    assert wr.BUDGETS[wr.DEFECT_BOUNTY_PREFIX]["cap"] == 30.0
    assert wr.BUDGETS[wr.DEFECT_BOUNTY_PREFIX]["monthly"] == 10.0


def test_the_defect_pot_is_not_released_yet():
    """Payments wait for the rules page. A bounty offered without published
    rules is an invitation to argue about them afterwards."""
    assert wr.DEFECT_BOUNTY_RELEASED_ON is None, (
        "flip this only when moltrust.ch/defects is live and approved"
    )


def test_the_legacy_cap_name_still_resolves():
    """Other code reads TEST_WALLET_CAP_USDC; it must not drift from BUDGETS."""
    assert wr.TEST_WALLET_CAP_USDC == wr.BUDGETS["test/bounty-r1"]["cap"]
