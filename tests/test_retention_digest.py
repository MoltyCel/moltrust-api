#!/usr/bin/env python3
"""DSGVO-Retention steht in der Sammelmeldung, nicht als eigene Meldung (ops KO1).

agents/retention_cleanup.py schickte bis 19.10.2026 bei jedem Lauf eine eigene
Telegram-Nachricht mit den Loeschzahlen. Die Zahlen sind Routine; sie stehen
jetzt als eine Zeile in scripts/selftest.py --digest. Der Abbruch (Rollup
gescheitert, nichts geloescht) bleibt ein Alarm und geht sofort raus, ueber
app/notify.py statt eines eigenen urlopen.

Jeder Fall hat eine Ausloese- und eine Schweigeprobe. Kein Test beruehrt
einen echten Zustandspfad: MOLTRUST_NOTIFY_STATE_DIR zeigt auf tmp_path.
"""
import asyncio
import datetime as dt
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import retention_status as R  # noqa: E402

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
LAUF = dt.datetime(2026, 10, 19, 3, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "state"
    monkeypatch.setenv("MOLTRUST_NOTIFY_STATE_DIR", str(d))
    return d


# 1) Die Zeile aus der Zustandsdatei

def test_state_path_follows_the_notify_state_dir(state_dir):
    assert R.state_path() == os.path.join(str(state_dir), R.FILENAME)


def test_trigger_successful_run_renders_the_counts():
    R.record(ok=True, request_log=1234, contact_inbox=2, days=31, now=LAUF)
    assert R.lines(NOW) == [
        "DSGVO Retention: 1234 request_log, 2 contact_inbox gelöscht, "
        "31 Tage verdichtet (Lauf 19.10. 03:30Z)"]


def test_trigger_aborted_run_is_named_as_aborted():
    R.record(ok=False, error="usage rollup failed: RuntimeError", now=LAUF)
    (line,) = R.lines(NOW)
    assert "abgebrochen (usage rollup failed: RuntimeError)" in line
    assert "request_log nicht bereinigt" in line


def test_trigger_missing_state_says_no_run():
    assert R.lines(NOW) == ["DSGVO Retention: kein Lauf verzeichnet"]


def test_trigger_stale_state_says_no_run_since():
    alt = NOW - dt.timedelta(hours=R.STALE_HOURS, minutes=1)
    R.record(ok=True, request_log=5, now=alt)
    assert R.lines(NOW) == [f"DSGVO Retention: kein Lauf seit {alt:%d.%m. %H:%M}Z"]


def test_silence_fresh_state_is_not_reported_missing():
    """Die laengste regulaere Luecke: 03:30-Lauf gegen die 20:00-Meldung."""
    R.record(ok=True, request_log=5, now=LAUF)
    abend = LAUF + dt.timedelta(hours=16, minutes=30)
    (line,) = R.lines(abend)
    assert "kein Lauf" not in line
    assert "5 request_log" in line


def test_unreadable_state_is_a_line_not_a_crash(state_dir):
    state_dir.mkdir()
    (state_dir / R.FILENAME).write_text("{kaputt")
    (line,) = R.lines(NOW)
    assert line.startswith("DSGVO Retention: Zustand nicht lesbar")


# 2) Der Lauf selbst, mit gefaelschter Datenbank

class _Conn:
    def __init__(self):
        self.sql = []

    async def execute(self, sql, *args):
        self.sql.append(sql)
        if "INSERT INTO known_callers" in sql:
            return "INSERT 0 3"
        if "DELETE FROM request_log" in sql:
            return "DELETE 1234"
        if "DELETE FROM contact_inbox" in sql:
            return "DELETE 2"
        return ""

    async def close(self):
        pass


def _run(monkeypatch, *, rollup_fails=False):
    from app import notify
    import agents.retention_cleanup as job

    conn = _Conn()

    async def connect(**kw):
        return conn

    monkeypatch.setitem(sys.modules, "asyncpg",
                        types.SimpleNamespace(connect=connect))

    usage = types.ModuleType("app.usage")

    async def rollup_days(conn, days_back):
        if rollup_fails:
            raise RuntimeError("boom")
        return 31

    async def prune_rollups(conn):
        return 0

    usage.rollup_days = rollup_days
    usage.prune_rollups = prune_rollups
    monkeypatch.setitem(sys.modules, "app.usage", usage)

    gesendet = []
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, **k: gesendet.append((text, k)) or True)
    asyncio.run(job.main())
    return conn, gesendet


def test_silence_successful_run_sends_nothing_itself(monkeypatch):
    conn, gesendet = _run(monkeypatch)
    assert gesendet == []
    assert any("DELETE FROM request_log" in s for s in conn.sql)
    (line,) = R.lines(dt.datetime.now(UTC))
    assert line.startswith("DSGVO Retention: 1234 request_log, 2 contact_inbox "
                           "gelöscht, 31 Tage verdichtet (Lauf ")


def test_trigger_aborted_rollup_alerts_immediately_via_notify(monkeypatch):
    from app import notify
    conn, gesendet = _run(monkeypatch, rollup_fails=True)
    assert len(gesendet) == 1
    text, kw = gesendet[0]
    assert text == ("Retention aborted: usage rollup failed (RuntimeError). "
                    "request_log was NOT pruned.")
    assert kw == {"channel": notify.ALERTS}
    assert not any("DELETE" in s for s in conn.sql)
    (line,) = R.lines(dt.datetime.now(UTC))
    assert "abgebrochen" in line


def test_the_job_has_no_sender_of_its_own():
    import agents.retention_cleanup as job
    src = open(job.__file__, encoding="utf-8").read()
    assert "urlopen" not in src
    assert "api.telegram.org" not in src
    assert not hasattr(job, "send_telegram")


# 3) Ende zu Ende durch send_digest: eine Meldung, die Zeile steht darin

def _digest(tmp_path, monkeypatch):
    from scripts import selftest
    from scripts import selftest_throttle as T
    out = tmp_path / "selftest-out"
    out.mkdir()
    monkeypatch.setattr(selftest, "OUTDIR", str(out))
    monkeypatch.setattr(T, "state", lambda *a, **k: {})
    monkeypatch.setattr(T, "save_state", lambda *a, **k: None)
    monkeypatch.setattr(T, "load_register", lambda *a, **k: [])
    monkeypatch.setattr(T, "load_open", lambda *a, **k: [])
    monkeypatch.setattr(T, "deploy_lines", lambda *a, **k: [])
    monkeypatch.delenv("HEALTHCHECK_SELFTEST_DIGEST_URL", raising=False)
    import scripts
    for name, attr in (("webroot_drift", "lines"), ("telegram_repeats", "lines"),
                       ("module_effects", "digest_lines"),
                       ("checkout_writes", "lines"), ("nginx_state", "lines"),
                       ("ref_guard", "lines"), ("claude_procs", "lines"),
                       ("check_radar_reading_list", "lines")):
        fake = types.ModuleType(f"scripts.{name}")
        setattr(fake, attr, lambda *a, **k: [])
        monkeypatch.setitem(sys.modules, f"scripts.{name}", fake)
        monkeypatch.setattr(scripts, name, fake, raising=False)
    import app
    koh = types.ModuleType("app.kohorten")
    koh.digest_lines = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, "app.kohorten", koh)
    monkeypatch.setattr(app, "kohorten", koh, raising=False)
    gesendet = []
    monkeypatch.setattr(selftest.notify, "send_telegram",
                        lambda text, **k: gesendet.append(text) or True)
    # Die Zustellung selbst wird geprueft, also braucht die Meldung einen
    # Handgriff (ops MELD-1).
    from tests.handgriff import erzwinge_handgriff
    erzwinge_handgriff(monkeypatch)
    assert selftest.send_digest() == 0
    assert len(gesendet) == 1
    return gesendet[0]


def test_trigger_digest_carries_the_retention_line(tmp_path, monkeypatch):
    R.record(ok=True, request_log=1234, contact_inbox=2, days=31,
             now=dt.datetime.now(UTC) - dt.timedelta(hours=1))
    text = _digest(tmp_path, monkeypatch)
    assert "DSGVO Retention: 1234 request_log, 2 contact_inbox gelöscht, " \
           "31 Tage verdichtet" in text


def test_trigger_digest_says_no_run_without_state(tmp_path, monkeypatch):
    text = _digest(tmp_path, monkeypatch)
    assert "DSGVO Retention: kein Lauf verzeichnet" in text
