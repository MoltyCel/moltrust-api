"""One message per outage, one reminder a day, one all-clear (ops WD-1).

From 2026-09-21 to 2026-10-16 the watchdog reported Herald twelve times a
day: the digest had moved to once a day at 12:00 and the threshold stayed at
12 h. Every message carried a new hour count, so the fingerprint throttle in
app/notify.py let each one through, and the watchdog kept no memory between
runs. These tests drive run() end to end over several simulated hours, with
every check replaced and the Telegram send captured.
"""
import datetime
import json
import sys
import types
from pathlib import Path

import pytest

import agents
from agents import watchdog as wd

T0 = datetime.datetime(2026, 10, 16, 1, 0, tzinfo=datetime.timezone.utc)
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Every check green unless a test turns it red; sends are captured."""
    monkeypatch.setattr(wd, "ALERT_STATE", str(tmp_path / "watchdog_alert_state.json"))
    monkeypatch.setattr(wd, "HEARTBEAT", str(tmp_path / "watchdog_heartbeat.json"))
    monkeypatch.setattr(wd, "ping_healthcheck", lambda ok: "test")

    red = set()
    sent = []
    deliver = {"ok": True}

    def verdict(key, detail="ok"):
        return {"ok": key not in red, "detail": f"{key} {'kaputt' if key in red else detail}"}

    def surfaced(key):
        return dict(verdict(key), surface=key)

    monkeypatch.setattr(wd, "check_heartbeat", lambda a, now: verdict(a["name"]))
    monkeypatch.setattr(wd, "check_conformance_drift", lambda: verdict("CONFORMANCE Drift"))
    monkeypatch.setattr(wd, "check_discovery_drift",
                        lambda now: [dict(verdict("Discovery/MCP"), surface="MCP")])
    monkeypatch.setattr(wd, "check_platform_id_drift",
                        lambda: [dict(verdict("PlatformId/card"), surface="card")])
    monkeypatch.setattr(wd, "_is_weekly_slot", lambda now, day: True)
    monkeypatch.setattr(wd, "check_anchor_proof_replay", lambda: verdict("AnchorProof"))
    monkeypatch.setattr(wd, "check_smithery_verification", lambda: [surfaced("SmitheryTXT")])
    monkeypatch.setattr(wd, "check_registry_matches_pypi", lambda: surfaced("Registry"))
    monkeypatch.setattr(wd, "check_withheld_rendering", lambda: [surfaced("Withheld")])
    monkeypatch.setattr(wd, "check_agent_card_signature",
                        lambda: [dict(verdict("CardSignature/jwks"), surface="jwks")])
    monkeypatch.setattr(wd.x_budget, "check", lambda auth, now: surfaced("XBudget"))
    monkeypatch.setattr(wd.x_meter, "check", lambda: surfaced("XSpend"))
    monkeypatch.setattr(wd, "check_radar_deadman", lambda now: surfaced("ReplyRadar"))

    x402 = types.ModuleType("scripts.x402_validator_check")
    x402.run_checks = lambda: [dict(verdict("x402Validator/bazaar"), name="bazaar")]
    monkeypatch.setitem(sys.modules, "scripts.x402_validator_check", x402)
    x_post = types.ModuleType("agents.x_post")
    x_post.get_auth = lambda: None
    monkeypatch.setitem(sys.modules, "agents.x_post", x_post)
    monkeypatch.setattr(agents, "x_post", x_post, raising=False)
    sup = types.ModuleType("agents.supervision")
    sup.load_expectations = lambda: {}
    sup.check_supervisor = lambda now, exp: {"light": "green", "check": "Supervisor", "detail": "ok"}
    monkeypatch.setitem(sys.modules, "agents.supervision", sup)
    monkeypatch.setattr(agents, "supervision", sup, raising=False)

    def send(msg, **kw):
        if deliver["ok"]:
            sent.append(msg)
        return deliver["ok"]

    monkeypatch.setattr(wd, "send_telegram", send)

    def run_at(hours):
        monkeypatch.setattr(wd, "_now", lambda: T0 + datetime.timedelta(hours=hours))
        wd.run()

    return types.SimpleNamespace(red=red, sent=sent, run_at=run_at, deliver=deliver,
                                 state=lambda: json.loads(Path(wd.ALERT_STATE).read_text()))


ALL_KEYS = ["Herald", "Scout", "Ambassador", "News Scout", "CONFORMANCE Drift",
            "Discovery/MCP", "PlatformId/card", "AnchorProof", "x402Validator/bazaar",
            "SmitheryTXT", "Registry", "Withheld", "CardSignature/jwks", "XBudget",
            "XSpend", "ReplyRadar"]


def test_an_outage_over_three_runs_is_one_message(harness):
    harness.red.add("Herald")
    for h in range(3):
        harness.run_at(h)
    assert len(harness.sent) == 1
    assert "Herald kaputt" in harness.sent[0]


def test_the_reminder_comes_after_a_day_and_not_before(harness):
    harness.red.add("Herald")
    for h in range(24):
        harness.run_at(h)
    assert len(harness.sent) == 1
    harness.run_at(24)
    assert len(harness.sent) == 2
    assert "Erinnerung, rot seit 24 h" in harness.sent[1]
    for h in range(25, 48):
        harness.run_at(h)
    assert len(harness.sent) == 2


def test_recovery_is_reported_once_and_then_silence(harness):
    harness.red.add("Herald")
    harness.run_at(0)
    harness.run_at(1)
    harness.red.clear()
    harness.run_at(2)
    harness.run_at(3)
    harness.run_at(4)
    assert len(harness.sent) == 2
    assert "Herald</b> wieder in Ordnung" in harness.sent[1]
    assert "Alert" not in harness.sent[1]


def test_a_new_outage_after_recovery_is_reported_again(harness):
    harness.red.add("Herald")
    harness.run_at(0)
    harness.red.clear()
    harness.run_at(1)
    harness.red.add("Herald")
    harness.run_at(2)
    assert len(harness.sent) == 3


def test_a_healthy_system_sends_nothing(harness):
    for h in range(5):
        harness.run_at(h)
    assert harness.sent == []


def test_the_state_survives_between_processes(harness):
    harness.red.add("Herald")
    harness.run_at(0)
    st = harness.state()
    assert st["Herald"]["ok"] is False and st["Herald"]["sent"].startswith("2026-10-16T01")
    assert st["Scout"]["ok"] is True


def test_an_undelivered_message_is_tried_again(harness):
    harness.red.add("Herald")
    harness.deliver["ok"] = False
    harness.run_at(0)
    harness.deliver["ok"] = True
    harness.run_at(1)
    harness.run_at(2)
    assert len(harness.sent) == 1


def test_every_check_of_the_watchdog_is_debounced(harness):
    """WD-1 criterion 5: the same rule for every service, not only Herald."""
    harness.red.update(ALL_KEYS)
    for h in range(3):
        harness.run_at(h)
    assert len(harness.sent) == 1
    for key in ALL_KEYS:
        assert f"{key} kaputt" in harness.sent[0], key
    harness.red.clear()
    harness.run_at(3)
    harness.run_at(4)
    assert len(harness.sent) == 2
    assert all(f"<b>{key}</b> wieder in Ordnung" in harness.sent[1] for key in ALL_KEYS)


def test_not_measured_neither_raises_nor_clears(harness, monkeypatch):
    harness.red.add("Registry")
    harness.run_at(0)
    monkeypatch.setattr(wd, "check_registry_matches_pypi",
                        lambda: {"ok": True, "unverifiable": True, "surface": "Registry",
                                 "detail": "PyPI nicht erreichbar"})
    harness.run_at(1)
    assert len(harness.sent) == 1
    assert harness.state()["Registry"]["ok"] is False


def test_an_unreadable_state_file_reports_once_more(harness):
    harness.red.add("Herald")
    harness.run_at(0)
    Path(wd.ALERT_STATE).write_text("{kaputt")
    harness.run_at(1)
    harness.run_at(2)
    assert len(harness.sent) == 2


# ── thresholds against the schedule ──

def test_the_longest_gap_of_a_schedule():
    assert wd.cron_gap_hours("0 12 * * * x") == 24
    assert wd.cron_gap_hours("0 8,18 * * * x") == 14
    assert wd.cron_gap_hours("*/30 * * * * x") == 0.5
    assert wd.cron_gap_hours("0 */6 * * * x") == 6
    assert wd.cron_gap_hours("0 9 * * 1 x") is None


def test_the_old_herald_threshold_is_a_finding(monkeypatch):
    tab = (REPO / "ops" / "crontab.txt").read_text()
    herald = next(a for a in wd.AGENTS if a["name"] == "Herald")
    monkeypatch.setitem(herald, "max_hours", 12)
    assert wd.threshold_findings(tab) == ["Herald: max 12 h, Takt 24 h"]


def test_the_checked_in_crontab_raises_no_threshold_finding():
    """Silence check: normal operation must not alarm."""
    tab = (REPO / "ops" / "crontab.txt").read_text()
    assert wd.threshold_findings(tab) == []


def test_herald_is_given_one_and_a_half_of_its_schedule():
    tab = (REPO / "ops" / "crontab.txt").read_text()
    line = next(l for l in tab.splitlines()
                if not l.startswith("#") and "agents/herald_v3.py digest" in l)
    herald = next(a for a in wd.AGENTS if a["name"] == "Herald")
    assert herald["max_hours"] == wd.cron_gap_hours(line) * 1.5


def test_an_agent_without_its_cron_line_is_a_finding():
    assert wd.threshold_findings("0 12 * * * agents/other.py\n")[0].startswith(
        "Herald: 0 Crontab-Zeilen")
