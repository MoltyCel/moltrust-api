"""The radar's threshold comes from its crontab, not from a number (ops CH8).

In October the radar ran at 06:05 and 16:05, a 14 h night pause, and the
watchdog held it to a fixed 12 h. Every night between 04:05 and 06:05 it
reported "Job lief nicht" for a job that was not due yet. WD-2 raised the
number and added c-watchdog-threshold-fits-cron to compare it with the
schedule; since CH8 the watchdog reads the schedule itself on every run.

The crontab lines below have the shape of the server's (secrets sourcing and
paths shortened); the night case is lines 183 and 184 of 16.10.2026.
"""
import datetime

from agents import watchdog as wd

UTC = datetime.timezone.utc
RADAR = "cd /home/moltstack/moltstack && venv/bin/python agents/reply_radar.py"
OCTOBER = "\n".join([
    f"5 16 * * * {RADAR} --no-search >> logs/reply_radar.log 2>&1",
    f"5 6 * * * {RADAR} --no-search >> logs/reply_radar.log 2>&1",
    f"*/5 * * * * {RADAR} --consume >> logs/reply_radar.log 2>&1",
])
# Today's: three daytime lines that expired on 18.10. and the 06:05 run.
GUARD = '[ "$(date -u +\\%Y\\%m\\%d)" -lt 20261018 ] &&'
TODAY = "\n".join([
    f"5 10 * * 1,3,5 {GUARD} {RADAR} >> logs/reply_radar.log 2>&1",
    f"5 10 * * 0,2,4,6 {GUARD} {RADAR} --no-search >> logs/reply_radar.log 2>&1",
    f"5 16 * * * {GUARD} {RADAR} --no-search >> logs/reply_radar.log 2>&1",
    f"5 6 * * * {RADAR} --no-search >> logs/reply_radar.log 2>&1",
    f"*/5 * * * * {RADAR} --consume >> logs/reply_radar.log 2>&1",
])


def night(hours_after_last_run: float) -> tuple:
    """A log whose last run was 16:05 with a reading list, and a now after it."""
    last = datetime.datetime(2026, 10, 15, 16, 5, 1, tzinfo=UTC)
    ts = last.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [f"[{ts}] INFO: REPLY RADAR — {ts[:10]} {ts[11:16]} UTC",
             f"[{ts}] INFO: Candidates after filtering: 6",
             f"[{ts}] INFO: reading list: 3 posts of 6 candidates sent"]
    return last + datetime.timedelta(hours=hours_after_last_run), lines


def test_the_october_schedule_gives_a_threshold_above_its_14_h_pause():
    limit, basis = wd.derived_max_hours(wd.RADAR_DEADMAN, OCTOBER)
    assert limit == 21
    assert basis == "Schwelle 21 h aus Takt 14 h"


def test_the_old_12_h_threshold_reports_a_run_that_is_not_due_yet():
    # Trigger for the old code path: 13 h after 16:05 is 05:05, an hour before
    # the next run. A fixed 12 h calls that a job that did not run.
    now, lines = night(13)
    assert wd.radar_silence(now, lines, 12)["reason"].startswith("Job lief nicht")


def test_the_derived_threshold_is_silent_in_the_night_pause(monkeypatch):
    now, lines = night(13)
    monkeypatch.setattr(wd, "_radar_tail", lambda *a, **k: lines)
    out = wd.check_radar_deadman(now, crontab=OCTOBER)
    assert out["ok"], out
    assert "Schwelle 21 h aus Takt 14 h" in out["detail"]


def test_the_derived_threshold_reports_a_missed_morning_run(monkeypatch):
    # 22 h after 16:05 is 14:05 the next day: 06:05 should have run and did
    # not. The message names the threshold and where it came from.
    now, lines = night(22)
    monkeypatch.setattr(wd, "_radar_tail", lambda *a, **k: lines)
    out = wd.check_radar_deadman(now, crontab=OCTOBER)
    assert not out["ok"]
    assert "Job lief nicht" in out["detail"]
    assert "Schwelle 21 h aus Takt 14 h" in out["detail"]


def test_todays_schedule_gives_the_36_h_that_was_set_by_hand():
    limit, basis = wd.derived_max_hours(wd.RADAR_DEADMAN, TODAY)
    assert limit == 36 == wd.RADAR_DEADMAN["max_hours"]
    assert basis == "Schwelle 36 h aus Takt 24 h"


def test_an_unreadable_crontab_falls_back_and_says_so(monkeypatch):
    limit, basis = wd.derived_max_hours(wd.RADAR_DEADMAN, None)
    assert limit == wd.RADAR_DEADMAN["max_hours"]
    assert "Rueckfall, Crontab nicht lesbar" in basis
    # The check still runs on the fallback and still reports.
    now, lines = night(40)
    monkeypatch.setattr(wd, "_radar_tail", lambda *a, **k: lines)
    monkeypatch.setattr(wd, "_crontab_text", lambda: None)
    out = wd.check_radar_deadman(now)
    assert not out["ok"]
    assert "Rueckfall" in out["detail"]


def test_a_crontab_without_radar_lines_falls_back_and_says_so():
    limit, basis = wd.derived_max_hours(
        wd.RADAR_DEADMAN, f"*/5 * * * * {RADAR} --consume >> x 2>&1")
    assert limit == wd.RADAR_DEADMAN["max_hours"]
    assert "0 Crontab-Zeilen" in basis and "Rueckfall" in basis


def test_the_fallback_still_fits_the_schedule():
    # c-watchdog-threshold-fits-cron keeps guarding the table value, which is
    # now the fallback: it must outlast today's schedule too.
    assert not [f for f in wd.threshold_findings(TODAY, keys=[])
                if f.startswith("ReplyRadar")]
