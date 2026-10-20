"""The X-budget check: the outage that went 44 hours unnoticed."""
import datetime
from pathlib import Path

import pytest

from agents import x_budget as xb


NOW = datetime.datetime(2026, 9, 27, 9, 0, tzinfo=datetime.timezone.utc)


class Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


DEPLETED = ('{"detail":"credits depleted","status":402,'
            '"title":"Payment Required"}')


def write_log(tmp_path, name, lines):
    (tmp_path / name).write_text("\n".join(lines) + "\n")


def test_a_depleted_read_is_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(xb.requests, "get", lambda *a, **k: Resp(402, DEPLETED))
    out = xb.check("auth", NOW, str(tmp_path))
    assert out["depleted"] and not out["ok"]
    assert "reads and writes" in out["detail"]


def test_a_working_read_is_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(xb.requests, "get", lambda *a, **k: Resp(200))
    assert xb.check("auth", NOW, str(tmp_path))["ok"]


def test_users_me_would_not_have_caught_it():
    """The probe must use an endpoint that consumes budget, not /2/users/me."""
    assert "users/me" not in xb.PROBE_URL
    assert xb.PROBE_URL.endswith("/tweets")


def test_the_first_402_of_today_is_found_and_attributed(tmp_path):
    write_log(tmp_path, "herald.log", [
        "[2026-09-27T12:00:13] ERROR: X API 402: " + DEPLETED,
    ])
    write_log(tmp_path, "reply_radar.log", [
        "[2026-09-27T08:05:04] WARNING: GET /2/lists -> 402: " + DEPLETED,
    ])
    found = xb.first_402_today(NOW, str(tmp_path))
    assert found["at"] == "2026-09-27T08:05:04"
    assert found["agent"] == "reply_radar.log"


def test_yesterdays_outage_does_not_alarm_today(tmp_path):
    write_log(tmp_path, "herald.log", [
        "[2026-09-26T12:00:13] ERROR: X API 402: " + DEPLETED,
    ])
    assert xb.first_402_today(NOW, str(tmp_path)) is None


def test_a_recovered_budget_still_reports_the_days_first_hit(monkeypatch, tmp_path):
    """Topped up mid-day, or running out in bursts — either is worth a line."""
    write_log(tmp_path, "herald.log", [
        "[2026-09-27T06:00:00] ERROR: X API 402: " + DEPLETED,
    ])
    monkeypatch.setattr(xb.requests, "get", lambda *a, **k: Resp(200))
    out = xb.check("auth", NOW, str(tmp_path))
    assert not out["ok"] and not out["depleted"]
    assert "reads answer again" in out["detail"]


def test_a_clean_log_and_a_clean_probe_alarm_on_nothing(monkeypatch, tmp_path):
    write_log(tmp_path, "herald.log", ["[2026-09-27T06:00:00] INFO: POSTED to X!"])
    monkeypatch.setattr(xb.requests, "get", lambda *a, **k: Resp(200))
    assert xb.check("auth", NOW, str(tmp_path))["ok"]


def test_missing_credentials_are_reported_not_swallowed(tmp_path):
    out = xb.check(None, NOW, str(tmp_path))
    assert not out["ok"] and "credentials" in out["detail"]


def test_a_network_failure_is_not_read_as_depleted(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(xb.requests, "get", boom)
    out = xb.check("auth", NOW, str(tmp_path))
    assert not out["ok"] and not out["depleted"], "an outage is not a budget problem"


@pytest.mark.parametrize("line", [
    '[2026-09-27T01:00:00] ERROR: {"detail":"credits depleted"}',
    "[2026-09-27T01:00:00] WARNING: -> 402: Payment Required",
])
def test_both_wordings_are_recognised(tmp_path, line):
    write_log(tmp_path, "a.log", [line])
    assert xb.first_402_today(NOW, str(tmp_path)) is not None


def test_the_watchdog_imports_without_the_oauth_stack():
    """CI's unit job installs no requests_oauthlib.

    A top-level `from agents import x_post` in watchdog.py made
    test_watchdog_weekly_checks.py fail to collect there, so the watchdog
    stopped being testable because of an optional check inside it.
    """
    source = (Path(__file__).resolve().parents[1] / "agents" / "watchdog.py").read_text()
    top_level = [l for l in source.splitlines()
                 if l.startswith(("import ", "from ")) and "x_post" in l]
    assert not top_level, f"x_post imported at module scope: {top_level}"
