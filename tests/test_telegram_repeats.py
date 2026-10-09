import datetime as dt
import json

from scripts import telegram_repeats as t

NOW = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.timezone.utc)


def _log(tmp_path, rows):
    p = tmp_path / "sent.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


def row(h, fp, ok=True, grund=None, text="x", kanal="alerts"):
    return {"ts": (NOW - dt.timedelta(hours=h)).isoformat(), "fingerabdruck": fp,
            "erfolg": ok, "grund": grund, "text_anfang": text, "kanal": kanal}


def test_a_message_sent_three_times_is_named(tmp_path):
    rows = [row(1, "a", text="HN Submit bereit"), row(1.5, "a", text="HN Submit bereit"),
            row(2, "a", text="HN Submit bereit"), row(3, "b")]
    out = t.lines(NOW, _log(tmp_path, rows))
    assert "2 Meldungsarten, 4 gesendet, 1 davon mehrfach gesendet" in out[0]
    assert out[1].strip().startswith("3x gesendet (3 Versuche, 0 gedrosselt) [alerts] HN Submit bereit")


def test_throttled_attempts_are_counted_but_not_as_sent(tmp_path):
    rows = [row(1, "a"), row(2, "a", ok=False, grund="gedrosselt")]
    out = t.lines(NOW, _log(tmp_path, rows))
    assert "1 gesendet, 0 davon mehrfach" in out[0]


def test_old_rows_are_outside_the_window(tmp_path):
    out = t.lines(NOW, _log(tmp_path, [row(30, "a"), row(31, "a")]))
    assert "0 Meldungsarten" in out[0]


def test_missing_log_is_a_line(tmp_path):
    assert "fehlt" in t.lines(NOW, str(tmp_path / "nope"))[0]
