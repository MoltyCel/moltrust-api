"""SUP-4: the weekly report's expected run count comes from supervise.yml.

It was `EXPECTED_PER_DAY = 24` ("runs at :17, every hour") while the workflow
had gone to `0 */6 * * *`, four a day, so every week reported runs missing that
were never due. The number now comes from the cron, and these tests read the
real workflow file rather than a copy of its schedule.
"""
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "supervise.yml"
_spec = importlib.util.spec_from_file_location(
    "supervision_report_sup4", ROOT / "scripts" / "supervision_report.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def test_the_real_workflow_schedules_four_runs_a_day():
    # The cron in the file is the source; asserted here so a change of cadence
    # shows up as a deliberate test edit, next to the comment in supervise.yml.
    assert sr.workflow_crons(str(WORKFLOW)) == ["0 */6 * * *"]
    assert sr.expected_per_day(str(WORKFLOW)) == 4
    assert sr.expected_per_day() == 4  # default path is the same file


def test_a_different_cron_gives_a_different_count(tmp_path):
    text = WORKFLOW.read_text()
    other = tmp_path / "supervise.yml"
    other.write_text(text.replace('cron: "0 */6 * * *"', 'cron: "17 * * * *"'))
    assert sr.expected_per_day(str(other)) == 24


def test_collect_uses_the_workflow_count(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "HISTORY", str(tmp_path / "none.jsonl"))
    monkeypatch.setattr(sr, "heal_state", lambda: str(tmp_path / "none.json"))
    for name in ("tick_ratio", "autofixes", "selftest_week", "activation",
                 "gate_usage"):
        monkeypatch.setattr(sr, name, lambda *a, **k: {})
    assert sr.collect(7)["expected_runs"] == 28


@pytest.mark.parametrize("spec,n", [
    ("0 */6 * * *", 4),
    ("17 * * * *", 24),
    ("0,30 9-17 * * *", 18),
    ("*/15 */2 * * *", 48),
    ("0 1-23/2 * * *", 12),
    ("5/20 3 * * *", 3),
    ("0 0 * * *", 1),
])
def test_runs_per_day(spec, n):
    assert sr.runs_per_day(spec) == n


@pytest.mark.parametrize("spec", [
    "0 */6 * *",          # four fields
    "0 */6 * * 1-5",      # weekdays only: the count depends on the date
    "0 0 1 * *",          # monthly
    "0 0 * 1 *",          # January only
    "@hourly",            # macro
    "0 */0 * * *",        # step zero
    "0 25 * * *",         # out of range
    "0 9-3 * * *",        # backwards range
    "x * * * *",
])
def test_an_unreadable_cron_fails_loudly(spec):
    with pytest.raises(sr.CronError):
        sr.runs_per_day(spec)


def test_a_workflow_without_a_cron_fails_loudly(tmp_path):
    p = tmp_path / "supervise.yml"
    p.write_text(re.sub(r"^\s*-\s*cron:.*$", "", WORKFLOW.read_text(), flags=re.M))
    with pytest.raises(sr.CronError):
        sr.expected_per_day(str(p))


def test_two_crons_fail_loudly(tmp_path):
    p = tmp_path / "supervise.yml"
    p.write_text(WORKFLOW.read_text().replace(
        '- cron: "0 */6 * * *"', '- cron: "0 */6 * * *"\n    - cron: "30 3 * * *"'))
    with pytest.raises(sr.CronError):
        sr.expected_per_day(str(p))


def test_main_exits_nonzero_instead_of_reporting_against_a_guess(monkeypatch, tmp_path,
                                                                 capsys):
    p = tmp_path / "supervise.yml"
    p.write_text(WORKFLOW.read_text().replace('"0 */6 * * *"', '"0 */6 * * 1-5"'))
    monkeypatch.setattr(sr, "SUPERVISE_WORKFLOW", str(p))
    monkeypatch.setattr(sr, "HISTORY", str(tmp_path / "none.jsonl"))
    monkeypatch.setattr(sr, "heal_state", lambda: str(tmp_path / "none.json"))
    assert sr.main(["--days", "7"]) == 2
    assert "Sollzahl nicht ableitbar" in capsys.readouterr().err


def test_no_fixed_daily_figure_left_in_the_script():
    src = (ROOT / "scripts" / "supervision_report.py").read_text()
    assert not re.search(r"^EXPECTED_PER_DAY\s*=", src, re.M)
