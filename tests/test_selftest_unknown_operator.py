"""One broken invariant file costs its own line, not the run (ops ST-5).

On 21.10.2026 c-app-info-reaches-journal arrived with operator ge. judge()
knew eq, lte and gte, raised KeyError, and every selftest run ended at that
file. The files behind it were not evaluated for two days, and nothing said so.
"""
import importlib.util
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


st = load("scripts/selftest.py", "selftest_unknown_op_mod")


def invariant(inv_id, operator, wert=1):
    return (f"id: {inv_id}\ntitel: {inv_id}\nkategorie: C\ntempo: hourly\n"
            f"abfrage:\n  art: shell\n  wert: echo 1\n"
            f"erwartung:\n  operator: {operator}\n  wert: {wert}\n"
            f"schweregrad: warn\nautofix: none\n")


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    """Run main() over a catalogue in tmp_path, with no database, network or send."""
    cat = tmp_path / "invariants"
    cat.mkdir()
    monkeypatch.setattr(st, "CATALOGUE", str(cat))
    monkeypatch.setattr(st, "OUTDIR", str(tmp_path / "out"))
    monkeypatch.setattr(st, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(st, "preflight", lambda: [])
    queried = []

    def run_query(inv):
        queried.append(inv["id"])
        return "1", None

    monkeypatch.setattr(st, "run_query", run_query)
    monkeypatch.setattr(st, "throttle", types.SimpleNamespace(
        load_register=lambda: [],
        classify=lambda found, entries, now, token="": {
            "neu": list(found), "verfallen": [], "bekannt": []},
        load_open=lambda: [],
        expired_open=lambda entries, now: [],
        save_open=lambda entries: None,
        drop_from_register=lambda ids: 0,
        drop_from_open=lambda ids: 0,
    ))
    monkeypatch.setattr(st.notify, "send_befunde",
                        lambda sofort, **kw: {"gedrosselt": []})

    def go(files):
        for name, text in files.items():
            (cat / f"{name}.yaml").write_text(text)
        monkeypatch.setattr(sys, "argv", ["selftest.py", "--tempo", "hourly",
                                          "--no-supervision"])
        code = st.main()
        out = tmp_path / "out"
        doc = json.loads(next(out.glob("*.json")).read_text())[-1]
        return code, doc, queried, capsys.readouterr().out

    return go


def test_an_invented_operator_costs_its_own_line(run):
    code, doc, queried, out = run({
        "a-first": invariant("a-first", "gte"),
        "b-invented": invariant("b-invented", "roughly"),
        "c-last": invariant("c-last", "eq"),
    })
    by_id = {r["id"]: r for r in doc["ergebnisse"]}
    assert by_id["a-first"]["status"] == "OK"
    assert by_id["c-last"]["status"] == "OK"
    bad = by_id["b-invented"]
    assert bad["status"] == "ERROR"
    assert bad["schweregrad"] == "fail"
    assert "unbekannter Operator 'roughly'" in bad["detail"]
    # The query of a file that cannot be judged is not run at all.
    assert queried == ["a-first", "c-last"]
    assert code != 0


def test_every_file_is_counted_and_the_run_reaches_the_last(run):
    files = {f"x{n:02d}": invariant(f"x{n:02d}", "gte") for n in range(12)}
    files["x05"] = invariant("x05", "ge")
    code, doc, queried, out = run(files)
    meta = doc["meta_invariante"]
    assert meta["erwartet"] == len(files)
    assert meta["ausgefuehrt"] == len(files)
    assert meta["lauf_vollstaendig"] is True
    assert [r["id"] for r in doc["ergebnisse"]][-1] == "x11"
    assert f"({len(files)}/{len(files)} ausgeführt)" in out
    assert code != 0


@pytest.mark.parametrize("erwartung, reason", [
    ({"operator": "ge", "wert": 1}, "unbekannter Operator 'ge'"),
    ({"wert": 1}, "keine Erwartung mit Operator"),
    ({"operator": "gte", "wert": "viele"}, "braucht einen Zahlenwert"),
])
def test_unjudgeable_names_the_reason(erwartung, reason):
    assert reason in st.unjudgeable({"erwartung": erwartung})


@pytest.mark.parametrize("operator, wert", [
    ("eq", 0), ("lte", 3), ("gte", 1), ("contains", "MATCH"), ("empty", ""),
])
def test_known_operators_pass(operator, wert):
    assert st.unjudgeable({"erwartung": {"operator": operator, "wert": wert}}) is None


def test_the_catalogue_in_the_repo_is_judgeable():
    catalogue = [i for i in st.load_catalogue() if not i.get("broken")]
    assert catalogue
    bad = {i.get("id", i["_file"]): st.unjudgeable(i) for i in catalogue
           if st.unjudgeable(i)}
    assert bad == {}
