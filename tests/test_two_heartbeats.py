"""Two heartbeats, two limits, and the distinction between them.

On 2026-10-04 two thresholds were set wrong in a row because one metric was
asked to answer for two things. data/supervise_heartbeat.json is stamped by
GitHub Actions and arrives about every 3.4 h at a 14 % delivery rate;
data/watchdog_heartbeat.json is stamped by the hourly cron on this machine. A
two-hour expectation belongs on the second. Putting it on the first measures
somebody else's queue, which is how it first came back red.
"""
import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "supervision", ROOT / "agents" / "supervision.py")
sv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sv)
CFG = yaml.safe_load((ROOT / "config" / "expectations.yaml").read_text())


def test_the_two_limits_sit_on_the_two_signals():
    assert CFG["supervisor"]["max_silence_minutes"] == 300
    assert CFG["watchdog"]["max_silence_minutes"] == 120
    assert CFG["supervisor"]["heartbeat"] != CFG["watchdog"]["heartbeat"]


def test_the_external_watch_is_declared_with_its_delivery_unverified():
    e = CFG["supervisor"]["external_watch"]
    assert e["check"] == "moltstack-watchdog"
    assert e["period_minutes"] == 60 and e["grace_minutes"] == 30
    # An alarm that has never fired is not proven to arrive, and the register
    # says so rather than implying otherwise.
    assert e["delivery_verified"] is False


def test_both_checks_run():
    src = (ROOT / "agents" / "supervision.py").read_text()
    assert "out.append(check_supervisor(now, spec))" in src
    assert "out.append(check_watchdog(now, spec))" in src


def test_a_missing_stamp_is_yellow_and_an_unreadable_one_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "BASE", str(tmp_path))
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)

    r = sv.check_watchdog(now, {"watchdog": {"heartbeat": "nope.json"}})
    assert r["light"] == sv.YELLOW

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    r = sv.check_watchdog(now, {"watchdog": {"heartbeat": "bad.json"}})
    assert r["light"] == sv.RED

    noday = tmp_path / "noday.json"
    noday.write_text('{"at": "irgendwann"}')
    r = sv.check_watchdog(now, {"watchdog": {"heartbeat": "noday.json"}})
    assert r["light"] == sv.RED


def test_the_stamp_is_written_after_the_ping():
    """The file says a run finished, not that one started.

    Checked inside run(), not over the whole file: `def stamp_heartbeat(now)`
    contains the call's own text and sits above the call, so a whole-file
    index() compares the definition against the call and passes or fails for
    the wrong reason. Third time today that a check matched text instead of
    behaviour.
    """
    src = (ROOT / "agents" / "watchdog.py").read_text()
    run = src.split("\ndef run():", 1)[1]
    assert "stamp_heartbeat(now)" in run
    assert run.index("ping_healthcheck(True)") < run.index("stamp_heartbeat(now)")


def test_the_channel_finding_is_recorded_as_read_not_assumed():
    e = CFG["supervisor"]["external_watch"]
    # Read with HEALTHCHECKS_API_KEY on 2026-10-05: one integration, email,
    # no Telegram. The flag that matters stays false either way.
    assert e["channels"] == 1
    assert e["channel_kinds"] == ["email"]
    assert e["telegram"] is False
    assert e["delivery_verified"] is False
    assert e["status"] == "up" and e["n_pings"] >= 27


def test_delivery_verified_may_not_be_set_by_configuration_alone():
    # The register says why in prose; this holds the pairing: as long as the
    # only channel is email and no test notification has been confirmed,
    # delivery_verified is false. A future true needs a measurement beside it.
    e = CFG["supervisor"]["external_watch"]
    if e["delivery_verified"] is True:
        assert "delivery_verified_how" in e, (
            "true braucht die Messung daneben, nicht nur die Konfiguration")
