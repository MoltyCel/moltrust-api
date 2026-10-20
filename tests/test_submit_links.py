"""Submit links: one message per post, %20, 80 characters (2026-10-09)."""
import datetime as dt
import json
import runpy
import urllib.parse

import pytest

from app import submit_links as sl

TITLE = "Show HN: We built a trust verification plugin for OpenClaw (W3C DID + reputation scoring)"


def test_title_is_cut_at_a_word_boundary_and_logged(caplog):
    with caplog.at_level("WARNING"):
        t = sl.clip_title(TITLE)
    assert len(t) <= 80 and TITLE.startswith(t) and not t.endswith(" ")
    assert "cut from 89" in caplog.text


def test_link_has_no_plus_and_spaces_are_percent_20():
    link = sl.hn_submit_link("https://moltrust.ch/blog/openclaw-plugin.html", TITLE)
    query = link.split("?", 1)[1]
    assert "+" not in query.replace("%2B", "")
    assert "%20" in query
    t = urllib.parse.parse_qs(query)["t"][0]
    assert len(t) <= 80 and "+" in t     # the '+' in "DID + reputation" survives as %2B


def _sender(results):
    calls = []
    def send(text, *, channel):
        calls.append(text)
        return results.pop(0)
    return send, calls


def test_a_post_is_announced_once(tmp_path):
    p = str(tmp_path / "s.json")
    send, calls = _sender([True])
    assert sl.send_once("hn:a", "x", channel="worklog", path=p, sender=send) == "sent"
    assert sl.send_once("hn:a", "x", channel="worklog", path=p, sender=send) == "already-sent"
    assert len(calls) == 1


def test_retry_only_after_a_recorded_failure_and_at_most_three(tmp_path):
    p = str(tmp_path / "s.json")
    send, calls = _sender([False, False, False, True])
    t0 = dt.datetime(2026, 10, 9, 12, tzinfo=dt.timezone.utc)
    out = [sl.send_once("hn:a", "x", channel="w", path=p, sender=send, now=t0 + dt.timedelta(minutes=i))
           for i in range(4)]
    assert out == ["failed", "failed", "failed", "gave-up"]
    assert len(calls) == 3


def test_an_attempt_without_outcome_is_not_repeated(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"hn:a": {"attempts": 1, "last_started_at": "2026-10-09T12:00:00+00:00"}}))
    send, calls = _sender([True])
    assert sl.send_once("hn:a", "x", channel="w", path=str(p), sender=send) == "outcome-unknown"
    assert calls == []


def test_loading_the_reminder_script_sends_nothing(monkeypatch):
    """The 2026-10-09 case: a probe that only loads the module must not send."""
    from app import notify
    sent = []
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: sent.append(a) or True)
    runpy.run_path("scripts/telegram_hn_remind.py", run_name="__nicht_main__")
    assert sent == []
