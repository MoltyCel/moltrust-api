"""scripts/wm3_growth.py: the WM-3 lines in the daily and weekly digests.

No database: the psql runner is replaced by a function that answers each query
constant with fixed rows. Every threshold-like output has a case that fires and
a case that stays quiet (n/a, reached, empty week).
"""
import datetime as dt
import pathlib

from scripts import wm3_growth as g

ROOT = pathlib.Path(__file__).resolve().parent.parent
BMAP = g.bucket_map((ROOT / "app" / "sql" / "registry_export.sql").read_text())
TODAY = dt.date(2026, 11, 23)  # a Monday


def runner(groups=(), registered=666, rate=14, cost="0", cohort=("0", "0", "0", "0")):
    answers = {
        g.Q_GROUPS_7D: [list(r) for r in groups],
        g.Q_GROUPS_LAST_WEEK: [list(r) for r in groups],
        g.Q_REGISTERED: [[str(registered)]],
        g.Q_RATE: [[str(rate)]],
        g.Q_BOUNTY_COST: [[cost]],
        g.Q_BOUNTY_COHORT: [list(cohort)],
    }
    return lambda sql: answers[sql]


def test_origin_groups_follow_the_registry_export():
    assert g.origin_group("usage-x402", "external", BMAP) == "usage"
    assert g.origin_group("paperclip", "external", BMAP) == "usage"
    assert g.origin_group("a2a", "external", BMAP) == "bounty"
    assert g.origin_group("moltbook", "external", BMAP) == "organic"
    assert g.origin_group("test", "external", BMAP) == "own_test"


def test_a_new_usage_value_is_usage_before_the_export_knows_it():
    assert g.origin_group("usage-api", "external", {}) == "usage"
    assert g.origin_group("usage-something", "external", BMAP) == "usage"


def test_system_agents_are_own_test_and_unknown_platforms_are_other():
    assert g.origin_group("moltbook", "system", BMAP) == "own_test"
    assert g.origin_group("nowhere-known", "external", BMAP) == "other"
    assert g.origin_group("", "external", BMAP) == "other"


def test_forecast_is_linear_on_the_14_day_rate():
    # 14 in 14 days = 1/day; 334 missing -> 334 days.
    assert g.forecast(666, 14, TODAY) == (TODAY + dt.timedelta(days=334)).isoformat()


def test_forecast_rounds_up_a_partial_day():
    assert g.forecast(999, 28, TODAY) == (TODAY + dt.timedelta(days=1)).isoformat()


def test_forecast_is_na_at_rate_zero_and_reached_at_target():
    assert g.forecast(666, 0, TODAY) == "n/a"
    assert g.forecast(1000, 0, TODAY) == "reached"
    assert g.forecast(1200, 5, TODAY) == "reached"


def test_bounty_before_any_round_is_zero_and_na():
    line = g.bounty_line(0.0, 0, 0, 0, 0)
    assert "cost 0.00 USDC" in line
    assert "valid 0" in line
    assert "cost/valid n/a" in line
    assert "error rate n/a" in line


def test_bounty_cost_per_valid_and_error_rate():
    line = g.bounty_line(2.5, 10, 12, 4, 3)
    assert "cost/valid 0.2500 USDC" in line
    assert "error rate 33% (4/12)" in line
    assert "pending 3" in line


def test_daily_lines_end_to_end():
    lines = g.daily_lines(run=runner(groups=[("usage-api", "external", "2"),
                                             ("moltbook", "external", "1"),
                                             ("test", "external", "5")]),
                          today=TODAY)
    assert lines[0] == "New registrations 7d by origin: +8 (usage 2, organic 1, own_test 5)"
    assert lines[1].startswith("Forecast registered 1000: 2027-10-23 ")
    assert "(registered 666, 14 in 14d without test/system)" in lines[1]
    assert lines[2].startswith("WM-3 bounty: cost 0.00 USDC, valid 0, cost/valid n/a")


def test_a_quiet_week_says_plus_zero_and_na():
    lines = g.daily_lines(run=runner(groups=[], rate=0), today=TODAY)
    assert lines[0] == "New registrations 7d by origin: +0"
    assert lines[1].startswith("Forecast registered 1000: n/a")


def test_weekly_lines_name_the_previous_iso_week():
    lines = g.weekly_lines(run=runner(groups=[("a2a", "external", "3")]), today=TODAY)
    assert lines[0] == "New registrations week 2026-W47 by origin: +3 (bounty 3)"


def test_a_failed_read_prints_not_available(monkeypatch, capsys):
    import subprocess

    def boom():
        raise subprocess.CalledProcessError(2, "psql")
    monkeypatch.setattr(g, "daily_lines", boom)
    assert g.main([]) == 1
    out = capsys.readouterr().out
    assert "not available" in out and "0" not in out.replace("CalledProcessError", "")


def test_both_digests_call_the_script():
    daily = (ROOT / "scripts" / "daily_stats.sh").read_text()
    weekly = (ROOT / "scripts" / "weekly_traffic.sh").read_text()
    assert "wm3_growth.py\" 2>/dev/null" in daily
    assert "wm3_growth.py\" --weekly" in weekly


def test_the_literal_sql_matches_the_constants():
    assert f"interval '{g.RATE_DAYS} days'" in g.Q_RATE
    assert f"LIKE '{g.BOUNTY_PREFIX}%'" in g.Q_BOUNTY_COST
    assert f"LIKE '{g.BOUNTY_PREFIX}%'" in g.Q_BOUNTY_COHORT
