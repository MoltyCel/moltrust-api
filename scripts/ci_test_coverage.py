#!/usr/bin/env python3
"""Which test files does the CI actually run?

Until 2026-10-17 the unit jobs named their test files one by one. A file
added to tests/ was collected by `pytest --collect-only` — imported, never
run — and nothing went red when it broke. tests/test_ar404_routes.py arrived
that way on 2026-10-16 and 152 of 208 test files sat in the same position.

This resolves coverage the only way that cannot lie: it reads every pytest
invocation out of the workflow files and asks pytest itself which files that
invocation would collect. Markers, --ignore, --deselect, -k and plain globs
are therefore all handled, because pytest handles them.

A `--collect-only` invocation does not count. Collection imports a module and
runs nothing, which is the gap this check exists to close.

    python3 scripts/ci_test_coverage.py            list the coverage per job
    python3 scripts/ci_test_coverage.py --check    exit 1 on an uncovered file
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
TESTS = ROOT / "tests"

# Files under tests/ that hold no tests. conftest.py is pytest's own hook
# module, __init__.py makes the package, ads_fakes.py is a fixture library
# imported by the suites that need it.
NOT_A_SUITE = {"__init__.py", "conftest.py", "ads_fakes.py"}

# A run step may hold several commands. Only the ones that start a pytest are
# of interest, and `python -m pytest` counts the same as a bare `pytest`.
PYTEST_LINE = re.compile(r"(?:^|[|;&]\s*)\s*(?:[A-Za-z0-9_]+=\S+\s+)*"
                         r"(?:python3?\s+-m\s+)?pytest\b")


def suite_files(repo: Path) -> set[str]:
    """Every file under tests/ that could hold a test, repo-relative."""
    return {
        f"tests/{p.name}"
        for p in sorted((repo / "tests").glob("*.py"))
        if p.name not in NOT_A_SUITE
    }


def pytest_invocations(wf: dict) -> list[tuple[str, str, dict, str]]:
    """(job id, pytest command, env, working directory) per pytest call."""
    found = []
    for job_id, job in (wf.get("jobs") or {}).items():
        job_dir = ((job.get("defaults") or {}).get("run") or {}).get(
            "working-directory", "")
        for step in job.get("steps") or []:
            run = step.get("run")
            if not run or "pytest" not in run:
                continue
            # sdk-tests runs in sdk/python, which sits outside `testpaths =
            # tests`. Resolving its invocation from the repo root would make
            # it look as if it covered the whole suite.
            workdir = step.get("working-directory", job_dir)
            env = {**(wf.get("env") or {}), **(job.get("env") or {}),
                   **(step.get("env") or {})}
            # Join continuation lines so a command split over several lines
            # arrives in one piece, then keep the pytest ones.
            joined = run.replace("\\\n", " ")
            for line in joined.splitlines():
                line = line.strip()
                if line.startswith("#") or not PYTEST_LINE.search(line):
                    continue
                found.append((job_id, line, env, workdir))
    return found


def collected_by(command: str, env: dict, cwd: Path) -> set[str]:
    """Ask pytest which files this invocation would collect.

    Returns an empty set for a --collect-only invocation: it imports the
    modules and runs nothing, so it covers nothing.
    """
    # Strip a shell pipeline (`| tail -30`), anything before a `;` that is not
    # the pytest itself (`set -o pipefail; pytest …`), and leading env
    # assignments. Every job in this repo is written in one of those shapes.
    command = command.split("|")[0].strip()
    command = command.split(";")[-1].strip()
    try:
        argv = shlex.split(command)
    except ValueError:
        return set()
    # Redirections survive shlex and pytest would read them as paths.
    argv = [a for a in argv if not re.fullmatch(r"\d*[<>]&?\d*", a)]
    while argv and "=" in argv[0] and not argv[0].startswith("-"):
        argv.pop(0)
    if argv[:1] == ["python"] or argv[:1] == ["python3"]:
        argv = argv[1:]
        if argv[:1] == ["-m"]:
            argv = argv[1:]
    if argv[:1] == ["pytest"]:
        argv = argv[1:]
    if "--collect-only" in argv:
        return set()

    # -q/-v and the reporting flags change nothing about what is collected;
    # keep them out so the output stays parsable.
    argv = [a for a in argv if a not in ("-q", "-v", "--tb=short")]

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "--no-header", "-p", "no:cacheprovider", *argv],
        cwd=cwd, env={**os.environ, **{k: str(v) for k, v in env.items()}},
        capture_output=True, text=True,
    )
    # Collected node ids look like `tests/test_x.py::test_name`. A collection
    # error prints the file too, and that file did get collected, so take it.
    files = set(re.findall(r"^(tests/[A-Za-z0-9_]+\.py)(?:::|\s|$)",
                           proc.stdout + proc.stderr, re.MULTILINE))
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when a file under tests/ runs in no job")
    ap.add_argument("--workflows", default=str(WORKFLOWS),
                    help="directory of workflow files (for the test suite)")
    ap.add_argument("--repo", default=str(ROOT),
                    help="checkout to resolve the invocations against")
    args = ap.parse_args()

    cwd = Path(args.repo)
    covered: set[str] = set()
    per_job: dict[str, set[str]] = {}
    for path in sorted(Path(args.workflows).glob("*.yml")):
        wf = yaml.safe_load(path.read_text())
        if not isinstance(wf, dict):
            continue
        for job_id, command, env, workdir in pytest_invocations(wf):
            if workdir:
                # Paths it collects are relative to that directory, so
                # nothing it finds is a file under the repo's tests/.
                per_job.setdefault(f"{path.name}:{job_id}", set())
                continue
            files = collected_by(command, env, cwd)
            per_job.setdefault(f"{path.name}:{job_id}", set()).update(files)
            covered |= files

    want = suite_files(cwd)
    missing = sorted(want - covered)

    for job, files in sorted(per_job.items()):
        print(f"{job}: {len(files)} Dateien")
    print(f"\ntests/: {len(want)} Suiten, {len(want & covered)} laufen, "
          f"{len(missing)} laufen in keinem Job")
    for f in missing:
        print(f"  UNCOVERED {f}")

    if args.check and missing:
        print(f"\nFAIL: {len(missing)} Testdatei(en) laufen in keinem Job. "
              f"Jede Datei unter tests/ gehoert in einen Job, der sie "
              f"ausfuehrt — --collect-only zaehlt nicht.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
