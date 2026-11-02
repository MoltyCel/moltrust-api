"""SV-2: a pipeline before its first due run is not red.

supervise reported pipeline/dauerrunde_woche red ("Beleg nicht lesbar —
dauerrunde_heartbeat.json: FileNotFoundError") before the cron had ever asked
for a run. Until the first due date plus max_silence_minutes, a pipeline
without evidence now reads "noch kein Lauf fällig"; after that the old rule
applies. These tests use the real register entry.
"""
import datetime
import importlib.util
import json
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
UTC = datetime.timezone.utc
FIRST_DUE = datetime.datetime(2026, 11, 2, 6, 0, tzinfo=UTC)  # Monday after #975
DEADLINE = FIRST_DUE + datetime.timedelta(minutes=11520)


def _supervision():
    spec = importlib.util.spec_from_file_location(
        "supervision_sv2", ROOT / "agents" / "supervision.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)
    return sv


def _declared():
    cfg = yaml.safe_load((ROOT / "config" / "expectations.yaml").read_text())
    return next(p for p in cfg["pipelines"] if p["name"] == "dauerrunde_woche")


@pytest.fixture
def sv(monkeypatch, tmp_path):
    sv = _supervision()
    monkeypatch.setattr(sv, "BASE", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    return sv


def _worst(findings):
    return max((f["light"] for f in findings), key=lambda l: {"green": 0, "yellow": 1, "red": 2}[l])


def test_the_first_due_date_comes_from_the_cron(sv):
    assert sv.first_due(_declared()) == FIRST_DUE


def test_silence_before_the_first_run_is_not_red(sv):
    # Schweigeprobe: no heartbeat file, first run not due yet.
    now = FIRST_DUE - datetime.timedelta(hours=1)
    out = sv.check_pipeline(_declared(), now)
    assert _worst(out) != "red"
    assert [f["check"] for f in out] == ["pipeline/dauerrunde_woche"]
    assert "noch kein Lauf fällig" in out[0]["detail"]


def test_the_grace_covers_the_allowance_after_the_first_date(sv):
    now = DEADLINE - datetime.timedelta(minutes=1)
    assert _worst(sv.check_pipeline(_declared(), now)) != "red"


def test_a_missed_first_run_stays_red(sv):
    # Auslöseprobe: first date plus allowance passed, still no evidence.
    out = sv.check_pipeline(_declared(), DEADLINE)
    assert _worst(out) == "red"
    assert "FileNotFoundError" in out[0]["detail"]


def test_an_unreadable_heartbeat_is_red_even_before_the_first_date(sv, tmp_path):
    # Missing and broken are different: only a missing file is "not yet".
    (tmp_path / "data" / "dauerrunde_heartbeat.json").write_text("{not json")
    out = sv.check_pipeline(_declared(), FIRST_DUE - datetime.timedelta(hours=1))
    assert _worst(out) == "red"
    assert "unparseable" in out[0]["detail"]


def test_a_run_before_the_first_date_is_judged_normally(sv, tmp_path):
    now = FIRST_DUE - datetime.timedelta(hours=1)
    (tmp_path / "data" / "dauerrunde_heartbeat.json").write_text(json.dumps(
        {"timestamp": now.isoformat(), "status": "fehler", "detail": "x"}))
    out = sv.check_pipeline(_declared(), now)
    assert any(f["check"] == "pipeline/dauerrunde_woche/status"
               and f["light"] == "red" for f in out)


def test_without_since_the_old_rule_applies(sv):
    spec = dict(_declared())
    spec.pop("since")
    out = sv.check_pipeline(spec, FIRST_DUE - datetime.timedelta(hours=1))
    assert _worst(out) == "red"


def test_an_unreadable_since_gives_no_grace(sv):
    spec = dict(_declared(), since="neulich")
    out = sv.check_pipeline(spec, FIRST_DUE - datetime.timedelta(hours=1))
    assert _worst(out) == "red"
