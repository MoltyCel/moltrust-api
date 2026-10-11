"""The scout runs from cron as `python -m workers.content_scout.pipeline`, so the
module body executes as __main__ and `main()` runs where the guard stands. From
#620 (2026-10-05) the guard sat above `enforce_point_class`; every run with a PASS
candidate died with NameError, eleven runs in a row on the server, while the unit
tests stayed green because they import the module and so define everything first.

The first test takes the cron path: runpy as __main__, one candidate the
classifier passes, all outside calls replaced. Without the fix it raises the same
NameError. The other two cover why nobody heard about it for six days: the run
now leaves a heartbeat, and supervision reads an `error` heartbeat as red.
"""
import datetime
import importlib.util
import json
import pathlib
import runpy
import sys
import warnings

import pytest
import yaml

from app import gh
from workers.content_scout import config, db, llm, pull, telegram

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Conn:
    def __init__(self):
        self.inserts = []

    async def execute(self, sql, *args):
        if "INSERT INTO content_review_queue" in sql:
            self.inserts.append(args)

    async def fetch(self, sql, *args):
        return []

    async def close(self):
        pass


def _stub_outside(monkeypatch, tmp_path):
    feed = tmp_path / "discovery_candidates.json"
    feed.write_text(json.dumps({"candidates": [{
        "url": "https://github.com/example/repo/issues/1",
        "title": "a thread", "repo": "example/repo", "number": 1}]}))
    conn = _Conn()

    async def connect(secrets):
        return conn

    async def seen_refs(c):
        return set()

    monkeypatch.setattr(config, "DISCOVERY_FEED", feed)
    monkeypatch.setattr(config, "NEWSSCOUT_ARTIFACT", tmp_path / "absent.json")
    monkeypatch.setattr(config, "load_secrets", lambda: {})
    monkeypatch.setattr(config, "anthropic_key", lambda secrets: "unused")
    monkeypatch.setattr(gh, "token", lambda: "unused")
    monkeypatch.setattr(llm, "make_client", lambda key: object())
    monkeypatch.setattr(llm, "reset_spend", lambda: None)
    monkeypatch.setattr(llm, "balance_ok", lambda client: True)
    monkeypatch.setattr(llm, "classify",
                        lambda client, system, text: {"verdict": "PASS", "reason": "r"})
    # A NONCONFORMANCE without locators, so the check has something to downgrade.
    monkeypatch.setattr(llm, "point",
                        lambda client, system, user: ("NONCONFORMANCE: x does y", "m"))
    monkeypatch.setattr(llm, "spend",
                        lambda: {"tokens_in": 0, "tokens_out": 0, "cost": 0.0})
    monkeypatch.setattr(pull, "pull_discovery", lambda url, token: "")
    monkeypatch.setattr(telegram, "send_summary", lambda secrets, text: None)
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: [])
    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(db, "seen_refs", seen_refs)
    monkeypatch.setattr(sys, "argv", ["pipeline"])
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))
    return conn


def _run_as_cron():
    with warnings.catch_warnings():
        # runpy warns when the module was already imported by another test.
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("workers.content_scout.pipeline", run_name="__main__")


def _heartbeat(tmp_path):
    return json.loads((tmp_path / "data" / "content_scout_heartbeat.json").read_text())


def test_cron_entry_point_reaches_the_point_class_check(monkeypatch, tmp_path, capsys):
    conn = _stub_outside(monkeypatch, tmp_path)
    _run_as_cron()

    assert len(conn.inserts) == 1
    lead_point = conn.inserts[0][7]
    assert lead_point.startswith("OBSERVATION:")
    assert "1 lead(s)" in capsys.readouterr().out
    assert _heartbeat(tmp_path)["status"] == "ok"


def test_a_run_that_raises_leaves_an_error_heartbeat(monkeypatch, tmp_path):
    _stub_outside(monkeypatch, tmp_path)

    def classify(client, system, text):
        raise NameError("name 'enforce_point_class' is not defined")

    monkeypatch.setattr(llm, "classify", classify)
    with pytest.raises(NameError):
        _run_as_cron()
    hb = _heartbeat(tmp_path)
    assert hb["status"] == "error"
    assert hb["detail"].startswith("NameError")


def _supervision():
    spec = importlib.util.spec_from_file_location(
        "supervision", ROOT / "agents" / "supervision.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)
    return sv


def _declared():
    cfg = yaml.safe_load((ROOT / "config" / "expectations.yaml").read_text())
    return next(p for p in cfg["pipelines"] if p["name"] == "content_scout")


@pytest.mark.parametrize("status, light", [("error", "red"), ("ok", "green")])
def test_supervision_reads_the_heartbeat(monkeypatch, tmp_path, status, light):
    sv = _supervision()
    monkeypatch.setattr(sv, "BASE", str(tmp_path))
    (tmp_path / "data").mkdir()
    now = datetime.datetime.now(datetime.timezone.utc)
    (tmp_path / "data" / "content_scout_heartbeat.json").write_text(json.dumps(
        {"timestamp": now.isoformat(), "status": status, "detail": "x"}))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "content_scout.log").write_text("")
    findings = sv.check_pipeline(_declared(), now)
    worst = "red" if any(f["light"] == "red" for f in findings) else "green"
    assert worst == light


def test_a_missed_run_is_a_finding(monkeypatch, tmp_path):
    sv = _supervision()
    monkeypatch.setattr(sv, "BASE", str(tmp_path))
    (tmp_path / "data").mkdir()
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = now - datetime.timedelta(hours=16)
    (tmp_path / "data" / "content_scout_heartbeat.json").write_text(json.dumps(
        {"timestamp": stale.isoformat(), "status": "ok", "detail": "x"}))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "content_scout.log").write_text("")
    findings = sv.check_pipeline(_declared(), now)
    assert any(f["light"] == "red" and "letzter Lauf" in f["detail"] for f in findings)
