"""scripts/check_meld1_zustellung.py: Befund und Schweigeprobe (ops MELD-1)."""
import datetime as dt
import json

from scripts import check_meld1_zustellung as c

NOW = dt.datetime(2026, 10, 31, 6, 0, tzinfo=dt.timezone.utc)


def _log(tmp_path, *zeilen):
    p = tmp_path / "telegram-sent.jsonl"
    p.write_text("".join(json.dumps(z) + "\n" for z in zeilen))
    return str(p)


def _z(ts, kanal="stats", erfolg=True, text="Lars muss: nichts\nSelbsttest"):
    return {"ts": ts, "kanal": kanal, "erfolg": erfolg, "text_anfang": text}


def test_a_delivered_report_without_a_step_fails(tmp_path):
    p = _log(tmp_path, _z("2026-10-30T20:00:21+00:00"))
    assert c.main(p, NOW) == 1


def test_a_worklog_one_counts_too(tmp_path):
    p = _log(tmp_path, _z("2026-10-30T20:00:21+00:00", kanal="worklog"))
    assert c.main(p, NOW) == 1


def test_normal_operation_is_silent(tmp_path):
    p = _log(tmp_path,
             _z("2026-10-30T20:00:21+00:00", text="Lars muss: Label setzen\nSelbsttest"),
             _z("2026-10-30T20:39:52+00:00", kanal="alerts"),
             _z("2026-10-30T21:00:00+00:00", erfolg=False),
             _z("2026-10-29T20:00:21+00:00"))  # before START: the finding itself
    assert c.main(p, NOW) == 0


def test_older_than_24_hours_drops_out(tmp_path):
    p = _log(tmp_path, _z("2026-10-30T13:00:00+00:00"))
    assert c.main(p, NOW + dt.timedelta(days=1)) == 0


def test_an_unreadable_log_is_not_a_pass(tmp_path):
    assert c.main(str(tmp_path / "fehlt.jsonl"), NOW) == 2
