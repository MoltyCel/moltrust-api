"""The register reads the watchdog's log with two patterns; every run must match them (ops SV-1).

config/expectations.yaml decides from logs/watchdog.log whether the watchdog
ran (run_marker) and whether it produced a verdict (output_marker). On
2026-10-16 the debounce of ops WD-1 (c75de6f) renamed the send line and stopped
printing "All agents healthy" while anything was red. Neither pattern matched
again, and supervise reported the watchdog as silent for two days while it ran
every hour.

So the patterns are read from the register and checked against what run()
actually logs, in each of its four endings. Changing a log line without the
register now fails here instead of in production.
"""
import logging
import re
from pathlib import Path

import pytest
import yaml

from agents import watchdog as wd
from tests.test_watchdog_debounce import harness  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[1]


def _spec() -> dict:
    reg = yaml.safe_load((REPO / "config" / "expectations.yaml").read_text())
    return next(p for p in reg["pipelines"] if p["name"] == "watchdog")


def _run(harness, caplog, hour: int) -> str:  # noqa: F811
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="watchdog"):
        harness.run_at(hour)
    return "\n".join(r.getMessage() for r in caplog.records)


def _matches(text: str) -> tuple[bool, bool]:
    spec = _spec()
    return (bool(re.search(spec["run_marker"], text)),
            bool(re.search(spec["output_marker"], text)))


def test_all_green_matches_both(harness, caplog):  # noqa: F811
    assert _matches(_run(harness, caplog, 0)) == (True, True)


def test_a_new_red_that_is_sent_matches_both(harness, caplog):  # noqa: F811
    harness.red.add("Herald")
    text = _run(harness, caplog, 0)
    assert "Sending" in text
    assert _matches(text) == (True, True)


def test_a_red_already_reported_matches_both(harness, caplog):  # noqa: F811
    """The ending that went unseen from 16.10.: red, debounced, nothing sent."""
    harness.red.add("Herald")
    _run(harness, caplog, 0)
    text = _run(harness, caplog, 1)
    assert "Sending" not in text and "All agents healthy" not in text
    assert _matches(text) == (True, True)


def test_unmeasured_surfaces_without_red_match_both(harness, caplog, monkeypatch):  # noqa: F811
    monkeypatch.setattr(wd, "check_registry_matches_pypi",
                        lambda: {"ok": True, "unverifiable": True, "surface": "Registry",
                                 "detail": "PyPI nicht erreichbar"})
    text = _run(harness, caplog, 0)
    assert "All agents healthy" not in text
    assert _matches(text) == (True, True)


def test_a_run_that_dies_before_the_end_matches_neither(harness, caplog, monkeypatch):  # noqa: F811
    """Firing probe: a crash mid-run is no run and no verdict."""
    def unreadable(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(wd, "load_alert_state", unreadable)
    with pytest.raises(OSError):
        _run(harness, caplog, 0)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert _matches(text) == (False, False)


def test_the_old_patterns_miss_the_debounced_ending(harness, caplog):  # noqa: F811
    """Guard against putting the pre-SV-1 patterns back."""
    old = "(?:All agents healthy|Sending alert for)"
    harness.red.add("Herald")
    _run(harness, caplog, 0)
    assert not re.search(old, _run(harness, caplog, 1))
