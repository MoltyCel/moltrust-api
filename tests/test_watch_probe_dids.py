"""scripts/watch_probe_dids.py (ops CRED-2410): every row is recorded, only a bypass attempt is sent."""
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("watch_probe_dids", ROOT / "scripts" / "watch_probe_dids.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

NOW = dt.datetime(2026, 10, 26, 9, 25, tzinfo=dt.timezone.utc)
DID = w.DIDS[0]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for name in ("STATE", "HEARTBEAT", "PROTOCOL"):
        monkeypatch.setattr(w, name, tmp_path / name.lower())
    sent = []
    fake = types.SimpleNamespace(ALERTS="alerts",
                                 send_telegram=lambda text, channel: sent.append(text) or True)
    monkeypatch.setitem(sys.modules, "app.notify", fake)
    import app
    monkeypatch.setattr(app, "notify", fake, raising=False)
    return tmp_path, sent


def fake(requests=(), gates=()):
    def query(sql, since):
        rows = requests if "request_log" in sql else gates
        return [list(r) for r in rows if r[1] > since]
    return query


def req(ts, what, code):
    return ("request", ts, DID, what, code, "")


def gate(ts, reason, detail=""):
    return ("gate", ts, DID, "/api/credential/issue", reason, detail)


def run(env, query):
    tmp, sent = env
    rc = w.main([], now=NOW, query=query)
    hb = json.loads((tmp / "heartbeat").read_text())
    return rc, hb, sent


def test_quiet_hour_sends_nothing(env):
    rc, hb, sent = run(env, fake())
    assert rc == 0 and hb["status"] == "ok" and sent == []


def test_ordinary_requests_are_recorded_not_sent(env):
    q = fake(requests=[req("2026-10-26 08:00:00+00", "POST /credentials/issue", "200"),
                       req("2026-10-26 08:01:00+00", "POST /credentials/verify", "200")],
             gates=[gate("2026-10-26 08:02:00+00", "score_withheld")])
    rc, hb, sent = run(env, q)
    assert sent == [] and hb["status"] == "ok"
    lines = (env[0] / "protocol").read_text().splitlines()
    assert len(lines) == 3 and not any(json.loads(l)["attempt"] for l in lines)


@pytest.mark.parametrize("row", [
    req("2026-10-26 08:00:00+00", "POST /credentials/issue", "400"),
    req("2026-10-26 08:00:00+00", "POST /credentials/issue", "403"),
    req("2026-10-26 08:00:00+00", "POST /canary/issue", "422"),
    gate("2026-10-26 08:00:00+00", "attestation_invalid", "bad signature"),
    gate("2026-10-26 08:00:00+00", "proof_replayed"),
])
def test_a_bypass_attempt_is_sent_once(env, row):
    q = fake(requests=[row] if row[0] == "request" else [], gates=[row] if row[0] == "gate" else [])
    rc, hb, sent = run(env, q)
    assert hb["status"] == "finding" and len(sent) == 1 and DID[-8:] in sent[0]
    rc, hb, sent = run(env, q)          # same rows, next hour
    assert hb["status"] == "ok" and len(sent) == 1


def test_a_404_or_a_401_is_not_an_attempt(env):
    q = fake(requests=[req("2026-10-26 08:00:00+00", "GET /credentials/status", "404"),
                       req("2026-10-26 08:00:01+00", "POST /credentials/issue", "401")])
    rc, hb, sent = run(env, q)
    assert sent == []


def test_a_failed_read_is_an_error_not_a_quiet_hour(env):
    def broken(sql, since):
        raise RuntimeError("psql: connection refused")
    rc, hb, sent = run(env, broken)
    assert rc == 1 and hb["status"] == "error" and sent == []


def test_the_watch_ends_by_itself(env):
    tmp, sent = env
    w.main([], now=w.WATCH_END + dt.timedelta(seconds=1), query=fake())
    assert json.loads((tmp / "heartbeat").read_text())["status"] == "expired"
