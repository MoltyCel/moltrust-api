"""scripts/check_disc1_zustellung.py: Befund und Schweigeprobe (ops DISC-1)."""
import datetime as dt
import json

from scripts import check_disc1_zustellung as c

NOW = dt.datetime(2026, 11, 1, 7, 0, tzinfo=dt.timezone.utc)
ALT = "\U0001f50d MolTrust Discovery: 11 new, 10 pruned, 145 total\n\n• pachaninm-lab/pachanin-demo#5469"


def _log(tmp_path, *zeilen):
    p = tmp_path / "telegram-sent.jsonl"
    p.write_text("".join(json.dumps(z) + "\n" for z in zeilen))
    return str(p)


def _z(ts, erfolg=True, text=ALT, kanal="worklog"):
    return {"ts": ts, "kanal": kanal, "erfolg": erfolg, "text_anfang": text[:80]}


def test_a_delivered_counter_line_fails(tmp_path):
    assert c.main(_log(tmp_path, _z("2026-11-01T06:01:03+00:00")), NOW) == 1


def test_normal_operation_is_silent(tmp_path):
    p = _log(tmp_path,
             _z("2026-11-01T06:01:03+00:00", text="Discovery 2026-11-01: 1 Thread für dich\nRückfragen im Console-Tab."),
             _z("2026-11-01T06:02:00+00:00", erfolg=False),
             _z("2026-10-31T06:01:02+00:00"))  # before START: the finding itself
    assert c.main(p, NOW) == 0


def test_older_than_24_hours_drops_out(tmp_path):
    later = NOW + dt.timedelta(days=2)
    assert c.main(_log(tmp_path, _z("2026-11-01T06:01:03+00:00")), later) == 0


def test_unreadable_log_exits_2(tmp_path):
    assert c.main(str(tmp_path / "missing.jsonl"), NOW) == 2
