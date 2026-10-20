"""Cron arithmetic and the three verdicts.

The check exists because "no scheduled runs" was ambiguous on 2026-10-03: an
hourly workflow four hours old and a Monday-morning workflow two days old both
had none, and only one of those was a question. So the cadence has to come from
the cron, and the warm-up window has to be declared.
"""
import datetime
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cer", ROOT / "scripts" / "check_external_runs.py")
cer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cer)

UTC = datetime.timezone.utc


def t(*a):
    return datetime.datetime(*a, tzinfo=UTC)


def test_hourly_at_17_matches_only_that_minute():
    assert cer.cron_matches("17 * * * *", t(2026, 10, 3, 16, 17))
    assert not cer.cron_matches("17 * * * *", t(2026, 10, 3, 16, 18))


def test_monday_morning_matches_only_monday():
    # 2026-10-05 is a Monday, 2026-10-04 a Sunday.
    assert cer.cron_matches("23 6 * * 1", t(2026, 10, 5, 6, 23))
    assert not cer.cron_matches("23 6 * * 1", t(2026, 10, 4, 6, 23))


def test_sunday_is_both_zero_and_seven():
    assert cer.cron_matches("30 6 * * 0", t(2026, 10, 4, 6, 30))


def test_lists_ranges_and_steps():
    assert cer.cron_matches("5 10 * * 1,3,5", t(2026, 10, 5, 10, 5))     # Monday
    assert not cer.cron_matches("5 10 * * 1,3,5", t(2026, 10, 4, 10, 5))  # Sunday
    assert cer.cron_matches("*/15 * * * *", t(2026, 10, 3, 9, 30))
    assert not cer.cron_matches("*/15 * * * *", t(2026, 10, 3, 9, 31))
    assert cer.cron_matches("0 7-9 * * *", t(2026, 10, 3, 8, 0))
    assert not cer.cron_matches("0 7-9 * * *", t(2026, 10, 3, 10, 0))


def test_previous_fires_counts_back_from_now():
    now = t(2026, 10, 3, 16, 30)
    assert cer.previous_fires("17 * * * *", now, count=2) == [
        t(2026, 10, 3, 16, 17), t(2026, 10, 3, 15, 17)]


def test_a_weekly_cron_is_not_measured_against_a_day():
    now = t(2026, 10, 3, 16, 30)          # Saturday
    fires = cer.previous_fires("23 6 * * 1", now, count=2)
    # The two previous Mondays, not the last two days.
    assert fires == [t(2026, 9, 28, 6, 23), t(2026, 9, 21, 6, 23)]


def test_the_deadline_sits_tolerated_misses_ticks_back():
    now = t(2026, 10, 3, 16, 30)
    fires = cer.previous_fires("17 * * * *", now, count=cer.TOLERATED_MISSES + 1)
    # The deadline is TOLERATED_MISSES ticks before the newest due one, so that
    # many dropped ticks pass without a finding. Measured on 2026-10-04:
    # GitHub delivers about one tick in five on this schedule, so the number is
    # two and the test follows the constant rather than restating it.
    assert len(fires) == cer.TOLERATED_MISSES + 1
    assert fires[0] == t(2026, 10, 3, 16, 17)
    assert fires[-1] == t(2026, 10, 3, 16 - cer.TOLERATED_MISSES, 17)


def test_the_tolerance_is_the_measured_one():
    # Changing it is allowed; changing it silently is not — the docstring has
    # to carry the measurement the number comes from.
    src = (ROOT / "scripts" / "check_external_runs.py").read_text()
    assert cer.TOLERATED_MISSES == 2
    assert "2026-10-04" in src and "3.4 h" in src


def test_the_declaration_finds_the_repo_schedules():
    rows = dict(cer.declared())
    assert "supervise.yml" in rows
    assert "17 * * * *" in rows["supervise.yml"]
    for name, crons in rows.items():
        for c in crons:
            assert len(c.split()) == 5, (name, c)


def test_a_deadline_before_the_file_existed_is_not_a_finding():
    # The shape of the bug this clause fixes: a weekly workflow added on
    # 1 October, measured against the Monday of 21 September.
    src = (ROOT / "scripts" / "check_external_runs.py").read_text()
    assert "earliest = (born + WARMUP) if born else None" in src
    assert "ZU JUNG" in src
    body = src.split("deadline = fires[-1]", 1)[1]
    # The guard comes before either finding, or it guards nothing — and after
    # the green case, or it swallows a schedule that did fire.
    assert body.index("ok {name}") < body.index("ZU JUNG")
    assert body.index("ZU JUNG") < body.index("NIE GEFEUERT")
