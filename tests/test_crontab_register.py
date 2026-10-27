"""The register, the snapshot and the live crontab name the same schedules (ops SV-1 K4)."""
import datetime
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "check_crontab_register", ROOT / "scripts" / "check_crontab_register.py")
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)

NOW = datetime.datetime(2026, 10, 27, 12, 0, tzinfo=datetime.timezone.utc)
WATCHDOG = "0 * * * * cd ~/moltstack && python agents/watchdog.py >> logs/watchdog.log 2>&1"
DIGEST = "0 11 * * * cd ~/moltstack && python agents/board_digest.py >> logs/board_digest.log 2>&1"
ONE_SHOT = "0 7 25 10 * cd ~/moltstack && python scripts/x_spend_after_radar.py --send"
REGISTER = {"pipelines": [
    {"name": "watchdog", "log": "logs/watchdog.log", "cron": ["0 * * * *"]},
    {"name": "board_digest", "log": "logs/board_digest.log", "cron": ["0 11 * * *"]},
    {"name": "x_meter", "log": "logs/watchdog.log", "cron": []},
]}


def run(register, snap, live):
    return reg.compare(register, "\n".join(snap), "\n".join(live), NOW)


def test_three_sources_in_line_are_silent():
    findings, infos, _ = run(REGISTER, [WATCHDOG, DIGEST], [WATCHDOG, DIGEST])
    assert findings == [] and infos == []


def test_an_expired_one_shot_is_info_not_a_finding():
    findings, infos, counts = run(REGISTER, [WATCHDOG, DIGEST, ONE_SHOT], [WATCHDOG, DIGEST])
    assert findings == []
    assert len(infos) == 1 and "x_spend_after_radar" in infos[0]
    assert counts.startswith("2 live, 3 snapshot")


def test_a_register_schedule_without_a_crontab_line_is_a_finding():
    findings, _, _ = run(REGISTER, [WATCHDOG], [WATCHDOG])
    assert findings == [
        "REGISTER board_digest '0 11 * * *' (logs/board_digest.log): no line in LIVE",
        "REGISTER board_digest '0 11 * * *' (logs/board_digest.log): no line in SNAPSHOT",
    ]


def test_a_missing_live_line_is_named_on_both_axes():
    findings, _, _ = run(REGISTER, [WATCHDOG, DIGEST], [WATCHDOG])
    assert any(f.startswith("SNAPSHOT, NOT LIVE: 0 11 * * *") for f in findings)
    assert "REGISTER board_digest '0 11 * * *' (logs/board_digest.log): no line in LIVE" in findings


def test_a_pipeline_run_at_an_undeclared_schedule_is_a_finding():
    extra = WATCHDOG.replace("0 * * * *", "30 * * * *", 1)
    findings, _, _ = run(REGISTER, [WATCHDOG, DIGEST, extra], [WATCHDOG, DIGEST, extra])
    assert sum("runs for watchdog" in f and "'30 * * * *'" in f for f in findings) == 2


def test_the_shipped_register_names_a_log_for_every_schedule():
    import yaml
    register = yaml.safe_load((ROOT / "config" / "expectations.yaml").read_text())
    for name, sched, log in reg.register_entries(register):
        assert log, f"{name} '{sched}' has no log to match on"


def test_the_shipped_register_matches_the_snapshot():
    import yaml
    register = yaml.safe_load((ROOT / "config" / "expectations.yaml").read_text())
    snap = (ROOT / "ops" / "crontab.txt").read_text()
    findings, _, _ = reg.compare(register, snap, snap, NOW)
    assert findings == []
