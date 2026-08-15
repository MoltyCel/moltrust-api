"""SCRATCH — proves the hardened collect step can fail. Reverted in the next commit.

Reproduces exactly the class of defect that used to pass silently: a module-scope
import that does not resolve, raising at collection time.
"""
import moltrust_ci_redproof_missing_module  # noqa: F401


def test_never_runs():
    assert True
