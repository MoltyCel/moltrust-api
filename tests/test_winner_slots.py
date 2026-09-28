"""The hundred-and-first qualifying submission gets nothing.

WINNER_SLOTS was a label until 2026-09-28: printed in the Telegram message and
ignored by the arithmetic. The Monday list came out at 133 paid slots of 75 bps
each while the task text promised "the reward is split equally between the first
100 submissions". These tests are what makes it a limit.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "payout", os.path.join(os.path.dirname(__file__), "..", "scripts",
                           "bounty_r2_payout_list.py"))
payout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(payout)


def _entries(n):
    return [{"did": f"did:moltrust:{i:016x}", "submitted_at": f"2026-09-23T{i:04d}"}
            for i in range(n)]


def test_the_hundred_and_first_gets_no_slot():
    paid, past, _, _ = payout.assign_shares(_entries(101), 100)
    assert len(paid) == 100
    assert len(past) == 1
    assert past[0]["did"] == _entries(101)[100]["did"]
    assert "bps" not in past[0]


def test_at_exactly_the_cap_every_share_is_equal():
    paid, past, base, rest = payout.assign_shares(_entries(100), 100)
    assert not past
    assert rest == 0
    assert {e["bps"] for e in paid} == {100}
    assert sum(e["bps"] for e in paid) == 10000


def test_far_over_the_cap_still_pays_exactly_the_cap():
    paid, past, _, _ = payout.assign_shares(_entries(171), 100)
    assert len(paid) == 100 and len(past) == 71
    assert sum(e["bps"] for e in paid) == 10000


def test_below_the_cap_the_remainder_goes_to_the_earliest():
    paid, past, base, rest = payout.assign_shares(_entries(7), 100)
    assert not past
    assert sum(e["bps"] for e in paid) == 10000
    assert paid[0]["bps"] >= paid[-1]["bps"]
    assert paid[0]["bps"] - paid[-1]["bps"] <= 1


def test_order_is_submission_time_not_input_order():
    e = _entries(3)[::-1]
    paid, _, _, _ = payout.assign_shares(e, 100)
    assert [x["submitted_at"] for x in paid] == sorted(x["submitted_at"] for x in e)
