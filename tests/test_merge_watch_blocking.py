"""blocking() must report a cancelled run even beside a newer green one of the same name.

Modelled on PR #620 on 2026-10-05: two runs each for `pytest --collect-only` and
`pytest (credit middleware)`, one cancelled at 20:24:51 and one successful at
20:29. Taking the newest per name reported all five required checks green, and the
merge was refused with "2 of 5 required status checks are cancelled".
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import merge_watch  # noqa: E402

PR620 = {"check_runs": [
    {"name": "pytest --collect-only", "status": "completed",
     "conclusion": "cancelled", "started_at": "2026-10-05T20:24:51Z"},
    {"name": "pytest --collect-only", "status": "completed",
     "conclusion": "success", "started_at": "2026-10-05T20:29:50Z"},
    {"name": "pytest (credit middleware)", "status": "completed",
     "conclusion": "cancelled", "started_at": "2026-10-05T20:24:51Z"},
    {"name": "pytest (credit middleware)", "status": "completed",
     "conclusion": "success", "started_at": "2026-10-05T20:29:41Z"},
    {"name": "bandit SAST (gating)", "status": "completed",
     "conclusion": "success", "started_at": "2026-10-05T20:29:32Z"},
    {"name": "pytest (onboarding routes)", "status": "completed",
     "conclusion": "success", "started_at": "2026-10-05T20:29:50Z"},
    {"name": "spec paths need lars-go", "status": "completed",
     "conclusion": "success", "started_at": "2026-10-05T20:30:29Z"},
]}


def _stub(monkeypatch, payload, status=200):
    monkeypatch.setattr(merge_watch, "call",
                        lambda method, path, token, body=None: (status, payload))


def test_a_cancelled_run_is_reported_beside_a_newer_green_one(monkeypatch):
    _stub(monkeypatch, PR620)
    out = merge_watch.blocking("o/r", "sha", "t")
    joined = " | ".join(out)
    assert "cancelled" in joined
    assert "pytest --collect-only" in joined
    assert "pytest (credit middleware)" in joined
    # Exactly the two cancelled ones, not the five successes.
    assert len(out) == 2, out


def test_the_newest_per_name_reading_would_have_reported_nothing(monkeypatch):
    """The regression this guards: dedup by name hides the blocker."""
    newest = {}
    for r in PR620["check_runs"]:
        prev = newest.get(r["name"])
        if prev is None or r["started_at"] >= prev["started_at"]:
            newest[r["name"]] = r
    hidden = [n for n, r in newest.items()
              if r["conclusion"] not in ("success", "neutral", "skipped")]
    assert hidden == [], "fixture no longer models the #620 situation"
    _stub(monkeypatch, PR620)
    assert merge_watch.blocking("o/r", "sha", "t"), \
        "blocking() reports nothing — the #620 defect is back"


def test_all_green_reports_nothing(monkeypatch):
    _stub(monkeypatch, {"check_runs": [
        {"name": "a", "status": "completed", "conclusion": "success",
         "started_at": "2026-10-05T20:00:00Z"}]})
    assert merge_watch.blocking("o/r", "sha", "t") == []


def test_queued_counts_as_blocking(monkeypatch):
    """A queued required check blocks too — it is not success yet."""
    _stub(monkeypatch, {"check_runs": [
        {"name": "a", "status": "queued", "conclusion": None,
         "started_at": None}]})
    out = merge_watch.blocking("o/r", "sha", "t")
    assert len(out) == 1 and "queued" in out[0]


def test_neutral_and_skipped_do_not_block(monkeypatch):
    _stub(monkeypatch, {"check_runs": [
        {"name": "a", "status": "completed", "conclusion": "neutral",
         "started_at": "2026-10-05T20:00:00Z"},
        {"name": "b", "status": "completed", "conclusion": "skipped",
         "started_at": "2026-10-05T20:00:00Z"}]})
    assert merge_watch.blocking("o/r", "sha", "t") == []


def test_an_unreadable_response_says_so(monkeypatch):
    _stub(monkeypatch, None, status=502)
    out = merge_watch.blocking("o/r", "sha", "t")
    assert len(out) == 1 and "502" in out[0]
