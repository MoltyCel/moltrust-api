"""The seven-day branch split, as the Sunday stats compute it."""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("sm_kpis", ROOT / "scripts" / "sm_kpis.py")
sm_kpis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm_kpis)


def iso(days_ago=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago)).isoformat()


def day(days_ago=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_each_branch_is_counted_on_its_own():
    state = {
        "drafts_by_source": {day(0): {"list": 3, "search": 2},
                             day(2): {"list": 1}},
        "decisions": {
            "1": {"source": "list", "verb": "post", "result": "posted", "at": iso(1)},
            "2": {"source": "list", "verb": "drop", "at": iso(1)},
            "3": {"source": "search", "verb": "drop", "at": iso(0)},
        },
    }
    b = sm_kpis._branch_split(state)
    assert b["list"] == {"sent": 4, "posted": 1, "drop": 1}
    assert b["search"] == {"sent": 2, "posted": 0, "drop": 1}


def test_what_fell_outside_the_window_is_left_out():
    state = {
        "drafts_by_source": {day(30): {"list": 9}},
        "decisions": {"1": {"source": "list", "verb": "post",
                            "result": "posted", "at": iso(30)}},
    }
    assert sm_kpis._branch_split(state) == {}


def test_decisions_from_before_the_source_was_recorded_are_ignored():
    """Not counted as a branch with zero — they belong to no branch at all."""
    state = {"decisions": {"1": {"verb": "drop", "at": iso(0)}}}
    assert sm_kpis._branch_split(state) == {}


def test_a_manual_post_counts_on_its_branch():
    state = {"decisions": {"1": {"source": "list", "verb": "post", "route": "manual",
                                 "result": "posted", "posted_at": iso(1)}}}
    assert sm_kpis._branch_split(state)["list"]["posted"] == 1


# ── the outage window ──

def test_outage_days_are_left_out_of_the_branch_split(monkeypatch):
    """25.-27.09. was three days of 402. They are not quiet days."""
    monkeypatch.setattr(sm_kpis, "OUTAGE_WINDOWS", [("2026-09-25", "2026-09-27")])
    state = {
        "drafts_by_source": {"2026-09-26": {"search": 5}, day(0): {"search": 2}},
        "decisions": {
            "1": {"source": "search", "verb": "drop", "at": "2026-09-26T10:00:00+00:00"},
            "2": {"source": "search", "verb": "drop", "at": iso(0)},
        },
    }
    b = sm_kpis._branch_split(state, days=30)
    assert b["search"] == {"sent": 2, "posted": 0, "drop": 1}, \
        "an outage day was counted"


def test_an_open_window_covers_everything_after_its_start(monkeypatch):
    monkeypatch.setattr(sm_kpis, "OUTAGE_WINDOWS", [("2026-09-25", None)])
    assert sm_kpis._in_outage("2026-09-26T10:00:00+00:00")
    assert sm_kpis._in_outage("2030-01-01T00:00:00+00:00")
    assert not sm_kpis._in_outage("2026-09-24T23:59:59+00:00")


def test_the_shipped_window_starts_on_the_day_of_the_first_402():
    assert sm_kpis.OUTAGE_WINDOWS[0][0] == "2026-09-25"
