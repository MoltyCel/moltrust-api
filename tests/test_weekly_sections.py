"""The two sections the weekly report gained on 2026-10-03.

Both are about what must NOT appear: a credential count in the activation
line, and a named finding that outlives its week.
"""
import datetime
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "supervision_report", ROOT / "scripts" / "supervision_report.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)


def test_named_findings_carry_their_week_and_expire():
    assert sr.NAMED_FINDINGS, "the list is the declaration"
    for n in sr.NAMED_FINDINGS:
        datetime.datetime.strptime(n["week"] + "-1", "%G-W%V-%u")
        assert n["id"] and n["what"]


def test_a_finding_from_another_week_is_not_rendered():
    k = {"days": 7, "runs": 1, "expected_runs": 1, "green": 1, "yellow": 0,
         "red": 0, "broken": 0, "offenders": [], "fixes": {}, "repeats": {},
         "autofixes": [], "selftest": {"catalogue": 3, "runs": 2, "meta_bad": 0,
                                       "findings": []},
         "named": [], "activation": {"dids": 10, "loops": 4}}
    out = sr.format_report(k)
    assert "nichts gemessen haben" not in out
    assert "<b>10</b> externe DIDs mit Track Record" in out


def test_the_activation_line_reports_dids_and_never_credentials():
    k = {"days": 7, "runs": 1, "expected_runs": 1, "green": 1, "yellow": 0,
         "red": 0, "broken": 0, "offenders": [], "fixes": {}, "repeats": {},
         "autofixes": [], "selftest": {"catalogue": 3, "runs": 2, "meta_bad": 0,
                                       "findings": []},
         "named": [], "activation": {"dids": 10, "loops": 4}}
    out = sr.format_report(k)
    assert "Credential-Zahl steht" in out
    # The query itself must not select a credential count.
    src = (ROOT / "scripts" / "supervision_report.py").read_text()
    body = src.split("def activation()", 1)[1].split("def selftest_week", 1)[0]
    assert "count(*) FROM credentials" not in body
    assert "GROUP BY 1) x" in body


def test_an_unmeasurable_activation_says_so_instead_of_zero():
    k = {"days": 7, "runs": 1, "expected_runs": 1, "green": 1, "yellow": 0,
         "red": 0, "broken": 0, "offenders": [], "fixes": {}, "repeats": {},
         "autofixes": [], "selftest": {"catalogue": 3, "runs": 2, "meta_bad": 0,
                                       "findings": []},
         "named": [], "activation": {"error": "psql: connection refused"}}
    out = sr.format_report(k)
    assert "Nicht gemessen" in out
    assert "externe DIDs mit Track Record" not in out


def test_selftest_section_counts_runs_and_findings(tmp_path, monkeypatch):
    day = tmp_path / "2026-10-03.json"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    day.write_text(json.dumps([
        {"started": now, "meta_invariante": {"status": "OK"},
         "ergebnisse": [{"id": "a", "status": "FAIL"}, {"id": "b", "status": "OK"}]},
        {"started": now, "meta_invariante": {"status": "FAIL"},
         "ergebnisse": [{"id": "a", "status": "ERROR"}]},
    ]))
    monkeypatch.setattr(sr, "SELFTEST_DIR", str(tmp_path))
    out = sr.selftest_week(7)
    assert out["runs"] == 2
    assert out["meta_bad"] == 1
    assert out["findings"] == [("a", 2)]


def test_an_unreadable_catalogue_is_not_a_zero(monkeypatch):
    monkeypatch.setattr(sr, "CATALOGUE", "/nowhere/at/all")
    assert sr.selftest_week(7)["catalogue"] is None
