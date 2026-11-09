"""Credential expiry warnings (config/zugaenge.yaml, scripts/zugang_ablauf.py).

Origin: Lars, 09.11.2026: the npm token moltrust-publish-3 expires on
07.02.2027, report it seven days before by Telegram. Each warning day has a
trigger test and the days around it a quiet test.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("zugang_ablauf", ROOT / "scripts" / "zugang_ablauf.py")
za = importlib.util.module_from_spec(spec)
spec.loader.exec_module(za)


def test_the_register_carries_the_npm_token_with_its_expiry():
    npm = [z for z in za.lesen() if z["name"] == "NPM_TOKEN"]
    assert len(npm) == 1 and npm[0]["ablauf"] == dt.date(2027, 2, 7)


def test_the_register_holds_no_secret():
    text = (ROOT / "config" / "zugaenge.yaml").read_text()
    assert "npm_" not in text and "_authToken" not in text


def test_seven_days_before_sends_one_message():
    calls = []
    JETZT = dt.datetime(2027, 1, 31, 9, tzinfo=dt.timezone.utc)
    rc = za.main([], jetzt=JETZT, send=lambda t, **kw: calls.append((t, kw)) or True)
    assert rc == 0 and len(calls) == 1
    text, kw = calls[0]
    assert kw == {"channel": za.notify.ALERTS, "art": "zugang-abgelaufen"}
    assert text.startswith("NPM_TOKEN läuft in 7 Tagen ab (07.02.2027)")
    assert len([z for z in text.splitlines() if z.strip()]) <= 4
    assert "Console-Tab" in text


def test_the_day_of_expiry_sends_again():
    calls = []
    JETZT = dt.datetime(2027, 2, 7, 9, tzinfo=dt.timezone.utc)
    za.main([], jetzt=JETZT, send=lambda t, **kw: calls.append(t) or True)
    assert len(calls) == 1 and "läuft heute ab" in calls[0]


def test_the_days_around_stay_quiet():
    for tag in (dt.datetime(2026, 11, 9, 9), dt.datetime(2027, 1, 30, 9),
                dt.datetime(2027, 2, 1, 9), dt.datetime(2027, 2, 6, 9), dt.datetime(2027, 2, 8, 9)):
        calls = []
        assert za.main([], jetzt=tag.replace(tzinfo=dt.timezone.utc), send=lambda t, **kw: calls.append(t) or True) == 0
        assert calls == [], tag


def test_a_probe_date_never_sends(capsys):
    calls = []
    assert za.main(["--heute", "2027-01-31"], send=lambda t, **kw: calls.append(t) or True) == 0
    assert calls == [] and "würde melden" in capsys.readouterr().out


def test_an_undelivered_warning_ends_with_the_mute_code():
    JETZT = dt.datetime(2027, 1, 31, 9, tzinfo=dt.timezone.utc)
    assert za.main([], jetzt=JETZT, send=lambda t, **kw: False) == za.notify.MUTE_EXIT_CODE

