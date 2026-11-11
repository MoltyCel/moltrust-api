"""The funnel and gate-proof scripts name their target and refuse production by default.

Both register agents. Run against https://api.moltrust.ch they land in the live
registry, as twelve did on 2026-09-19 and three more from gate_proof.py. The
refusal happens after argument parsing and before the first request, so these
tests make no network call.
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = [
    ("scripts/funnel_test.py", "FUNNEL_API_BASE", ["--classes", "K5", "--out", os.devnull]),
    ("scripts/gate_proof.py", "GATE_PROOF_API_BASE", ["--probe-only"]),
]


def _run(script, args, env):
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run([sys.executable, str(ROOT / script), *args], env={**base, **env},
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("script,var,args", CASES)
def test_no_target_is_refused(script, var, args):
    r = _run(script, args, {})
    assert r.returncode != 0
    assert f"set {var}" in r.stderr


@pytest.mark.parametrize("script,var,args", CASES)
@pytest.mark.parametrize("prod", ["https://api.moltrust.ch", "https://api.moltrust.ch/"])
def test_production_without_the_word_is_refused(script, var, args, prod):
    r = _run(script, args, {var: prod})
    assert r.returncode != 0
    assert "is production" in r.stderr
