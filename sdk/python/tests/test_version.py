"""`__version__` and the package version are one number.

The 0.6.0 wheel on PyPI reports `moltrust_enforce.__version__ == "0.5.0"`: the
release bumped pyproject.toml and left `__init__.py` behind. Found 2026-10-11.
"""
import pathlib
import re

import moltrust_enforce

PYPROJECT = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_dunder_version_matches_pyproject():
    m = re.search(r'^version = "([^"]+)"$', PYPROJECT.read_text(), re.M)
    assert m, "no version line in pyproject.toml"
    assert moltrust_enforce.__version__ == m.group(1)
