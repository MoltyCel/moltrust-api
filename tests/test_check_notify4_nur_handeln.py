"""c-notify-4: the check over the send log, with its counter-checks."""
import datetime as dt
import json

from scripts import check_notify4_nur_handeln as C

NOW = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)


def _log(tmp_path, *zeilen):
    p = tmp_path / "sent.jsonl"
    p.write_text("".join(json.dumps(z) + "\n" for z in zeilen))
    return str(p)


def _z(minuten, **kw):
    z = {"ts": (NOW - dt.timedelta(minutes=minuten)).isoformat(), "kanal": "stats",
         "erfolg": True, "text_anfang": "x"}
    z.update(kw)
    return z


def test_a_delivered_handeln_line_holds(tmp_path, capsys):
    assert C.main(_log(tmp_path, _z(5, art="reply-entwurf", klasse="HANDELN")), NOW) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "0"


def test_a_filed_line_holds(tmp_path):
    assert C.main(_log(tmp_path, _z(5, art="deploy", klasse="ABLAGE", erfolg=False,
                                    grund="ablage")), NOW) == 0


def test_a_delivered_ablage_line_is_a_finding(tmp_path, capsys):
    assert C.main(_log(tmp_path, _z(5, art="deploy", klasse="ABLAGE")), NOW) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == "1"


def test_an_unclassified_delivery_is_a_finding(tmp_path):
    assert C.main(_log(tmp_path, _z(5, art="?", klasse="HANDELN")), NOW) == 1


def test_old_lines_and_lines_without_class_do_not_count(tmp_path):
    assert C.main(_log(tmp_path, _z(60 * 25, art="deploy", klasse="ABLAGE"),
                       _z(5)), NOW) == 0


def test_unreadable_log(tmp_path, capsys):
    assert C.main(str(tmp_path / "fehlt.jsonl"), NOW) == 2
    assert "UNREADABLE" in capsys.readouterr().out
