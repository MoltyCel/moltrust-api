"""The coverage check has to go red when a suite runs in no job.

A guard that cannot fail proves nothing. Until 2026-10-17 the CI jobs named
their test files one by one and 152 of 208 suites ran nowhere; the check in
scripts/ci_test_coverage.py exists so that state cannot come back quietly.
These cases run it against workflows built for the purpose, in a throwaway
checkout, so they assert the check's behaviour and not today's real workflow.

Runs with --noconftest in CI: nothing here needs the API rig.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "ci_test_coverage.py"


def build_repo(tmp_path: Path, suites: list[str]) -> Path:
    """A minimal checkout with a tests/ directory and nothing else."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    for name in suites:
        (repo / "tests" / name).write_text("def test_ok():\n    assert True\n")
    # The real tests/ has these three and they hold no tests; the check has
    # to skip them, so put them in the way.
    (repo / "tests" / "__init__.py").write_text("")
    (repo / "tests" / "conftest.py").write_text("")
    (repo / "tests" / "ads_fakes.py").write_text("FAKE = 1\n")
    return repo


def build_workflow(tmp_path: Path, run_lines: list[str]) -> Path:
    """A workflow directory holding one job per given run line."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir(exist_ok=True)
    steps = "\n".join(
        f"      - name: step {i}\n        run: {line}" for i, line in enumerate(run_lines)
    )
    (wf_dir / "ci.yml").write_text(
        "name: t\non: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n"
        + steps
        + "\n"
    )
    return wf_dir


def run_check(repo: Path, workflows: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--check",
         "--repo", str(repo), "--workflows", str(workflows)],
        capture_output=True, text=True,
    )


def test_a_left_out_suite_turns_the_check_red(tmp_path):
    """The case the check exists for: one file in no job at all."""
    repo = build_repo(tmp_path, ["test_a.py", "test_b.py"])
    wf = build_workflow(tmp_path, ["pytest -q tests/test_a.py"])

    res = run_check(repo, wf)

    assert res.returncode == 1, res.stdout + res.stderr
    assert "test_b.py" in res.stdout
    assert "UNCOVERED" in res.stdout


def test_a_directory_covers_every_suite_in_it(tmp_path):
    """The fix: naming the directory covers the file added tomorrow."""
    repo = build_repo(tmp_path, ["test_a.py", "test_b.py", "test_tomorrow.py"])
    wf = build_workflow(tmp_path, ["pytest -q tests/"])

    res = run_check(repo, wf)

    assert res.returncode == 0, res.stdout + res.stderr
    assert "UNCOVERED" not in res.stdout


def test_collect_only_covers_nothing(tmp_path):
    """Importing a module is not running it.

    This is the distinction the whole check rests on: before 2026-10-17 the
    collect-only job named tests/ and every suite looked covered, while 152
    of them were never executed.
    """
    repo = build_repo(tmp_path, ["test_a.py"])
    wf = build_workflow(tmp_path, ["pytest --collect-only -q tests/"])

    res = run_check(repo, wf)

    assert res.returncode == 1, res.stdout + res.stderr
    assert "test_a.py" in res.stdout


def test_the_helper_files_are_not_suites(tmp_path):
    """conftest.py, __init__.py and ads_fakes.py hold no tests.

    Counting them would make the check permanently red for a reason nobody
    can fix, and a check that is always red gets switched off.
    """
    repo = build_repo(tmp_path, ["test_a.py"])
    wf = build_workflow(tmp_path, ["pytest -q tests/"])

    res = run_check(repo, wf)

    assert res.returncode == 0, res.stdout + res.stderr
    for helper in ("conftest.py", "__init__.py", "ads_fakes.py"):
        assert helper not in res.stdout


def test_a_pipeline_after_pytest_does_not_hide_the_paths(tmp_path):
    """`pytest … 2>&1 | tail -30` is how every job in this repo is written."""
    repo = build_repo(tmp_path, ["test_a.py"])
    wf = build_workflow(tmp_path, ["set -o pipefail; pytest -q tests/ 2>&1 | tail -30"])

    res = run_check(repo, wf)

    assert res.returncode == 0, res.stdout + res.stderr


def test_python_m_pytest_counts_the_same(tmp_path):
    """pytest-run invokes it as `python -m pytest`."""
    repo = build_repo(tmp_path, ["test_a.py"])
    wf = build_workflow(tmp_path, ["python -m pytest tests/test_a.py -v --tb=short"])

    res = run_check(repo, wf)

    assert res.returncode == 0, res.stdout + res.stderr


def test_the_real_workflows_leave_no_suite_behind():
    """The repo as it stands, not a fixture.

    This is the same assertion the CI step makes; having it here means a
    local run catches a new suite before the push does.
    """
    res = subprocess.run(
        [sys.executable, str(CHECK), "--check"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    if res.returncode == 2:
        pytest.skip("check could not run here: " + res.stderr.strip()[:200])
    assert res.returncode == 0, res.stdout + res.stderr
