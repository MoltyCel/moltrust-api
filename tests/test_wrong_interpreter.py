"""A missing library is a statement about us, not about the dependency.

On 2026-10-03 a hand-run of the invariant runner under the system python3
reported dep/x and dep/bluesky red. Both were green the whole time;
requests_oauthlib and atproto live in the venv. Red there would page for our
own interpreter, and the report would carry an X outage that never happened.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


st = load("scripts/selftest.py", "selftest_pf")


def test_preflight_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(st, "NEEDED", ("json", "os", "a_module_that_is_not_here"))
    assert st.preflight() == ["a_module_that_is_not_here"]


def test_preflight_is_quiet_when_everything_is_there(monkeypatch):
    monkeypatch.setattr(st, "NEEDED", ("json", "os", "sys"))
    assert st.preflight() == []


def test_the_declared_set_is_what_the_checks_reach_for():
    # If a check starts importing something new, it belongs here too — the
    # list is the declaration, and an undeclared import brings the false red
    # back.
    assert "requests_oauthlib" in st.NEEDED
    assert "yaml" in st.NEEDED


def test_supervision_downgrades_a_missing_module():
    src = (ROOT / "agents" / "supervision.py").read_text()
    # dep_x, dep_bluesky and the generic wrapper each have to distinguish the
    # two cases; a single one of them left on RED is enough to page again.
    assert src.count("ModuleNotFoundError") >= 3
    assert "Befund über die Abhängigkeit" in src
    assert src.count("unverifiable=True") >= 3
