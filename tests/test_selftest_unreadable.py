"""Not knowing is not a pass, and a known state is not a new alarm.

Both rules come from the same morning. b-registry-equals-pypi compared PyPI
against the oldest of five registry entries and reported a drift that did not
exist — as a WARN, which is a verdict about the world rather than about the
parser. And a-track-record-burst is red for a reason that was decided, not
discovered, so it has to stay measured without reading as news.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


st = load("scripts/selftest.py", "selftest_mod")
ccgu = load("scripts/check_green_on_unreadable.py", "ccgu_mod")


# --- unreadable --------------------------------------------------------------

@pytest.mark.parametrize("value, expect", [
    ("pypi=1.2.4 registry=1.2.4\nMATCH", None),
    ("0", None),
    ("17", None),
    ("UNREADABLE registry: HTTPError", "registry: HTTPError"),
    ("pypi=1.2.4\nUNREADABLE registry: 5 entries carry version=latest",
     "registry: 5 entries carry version=latest"),
    ("", "leere Antwort"),
    ("   ", "leere Antwort"),
    ("pypi=1.2.4 registry=?\n?", "Platzhalter statt Messwert: '?'"),
    ("None", "Platzhalter statt Messwert: 'None'"),
])
def test_unreadable_recognises_the_shapes(value, expect):
    assert st.unreadable(value) == expect


def test_a_drift_is_still_a_drift():
    # The point is not to swallow real findings. DRIFT is a measurement.
    assert st.unreadable("pypi=1.2.4 registry=1.2.3\nDRIFT 1.2.4 vs 1.2.3") is None


# --- the auditor -------------------------------------------------------------

def scan_src(tmp_path, src):
    f = tmp_path / "w.py"
    f.write_text(src)
    return ccgu.scan(f)


def test_green_inside_an_except_branch_is_a_finding(tmp_path):
    out = scan_src(tmp_path, """
def check():
    try:
        v = look()
    except Exception as e:
        return {"surface": "X", "ok": True, "detail": f"unreachable ({e})"}
    return {"surface": "X", "ok": v == 1, "detail": "measured"}
""")
    kinds = [k for _, k, _ in out]
    assert kinds == ["EXCEPT"]
    assert "check()" in out[0][2]


def test_declaring_it_unverifiable_clears_the_finding(tmp_path):
    out = scan_src(tmp_path, """
def check():
    try:
        v = look()
    except Exception as e:
        return {"surface": "X", "ok": True, "unverifiable": True,
                "detail": f"unreachable ({e})"}
    return {"surface": "X", "ok": v == 1, "detail": "measured"}
""")
    assert out == []


def test_a_blind_detail_outside_an_except_branch_is_a_finding(tmp_path):
    out = scan_src(tmp_path, """
def check():
    if dns() is None:
        return {"surface": "TXT", "ok": True, "detail": "DNS not answerable, skipped"}
    return {"surface": "TXT", "ok": True, "detail": "record present"}
""")
    assert [k for _, k, _ in out] == ["BLIND"]


def test_an_honest_failure_verdict_is_not_a_finding(tmp_path):
    out = scan_src(tmp_path, """
def check():
    try:
        v = look()
    except Exception as e:
        return {"surface": "X", "ok": False, "detail": f"unreachable ({e})"}
    return {"surface": "X", "ok": True, "detail": "measured"}
""")
    assert out == []


def test_the_live_watchers_are_clean():
    # The retroactive run of 2026-10-03 found thirteen; they are declared now,
    # and this is what stops them coming back one commit at a time.
    total = 0
    for rel in ccgu.WATCHERS:
        path = ccgu.ROOT / rel
        assert path.exists(), f"{rel} is declared as a watcher and missing"
        total += len(ccgu.scan(path))
    assert total == 0, "a watcher reports green on something it could not read"
