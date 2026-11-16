"""Credential expiry warnings (config/zugaenge.yaml, scripts/zugang_ablauf.py).

Origin: Lars, 09.11.2026: the npm token moltrust-publish-3 expires on
07.02.2027, report it seven days before by Telegram. Each warning day has a
trigger test and the days around it a quiet test.

The token was deleted on 16.11.2026 (ops NPM-TP, Trusted Publishing), so the
real register is empty; the warning tests run against a register of their own
with an entry of the same shape.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("zugang_ablauf", ROOT / "scripts" / "zugang_ablauf.py")
za = importlib.util.module_from_spec(spec)
spec.loader.exec_module(za)


BEISPIEL = """zugaenge:
  - name: NPM_TOKEN
    beschreibung: npm-Token moltrust-publish-3 (Scope @moltrust)
    ablauf: 2027-02-07
    erneuern: >-
      Neuen Token anlegen und in ~/.moltrust_secrets eintragen.
"""


@pytest.fixture
def register(tmp_path):
    p = tmp_path / "zugaenge.yaml"
    p.write_text(BEISPIEL, encoding="utf-8")
    return ["--register", str(p)]


def test_the_retired_npm_token_is_out_of_the_register():
    assert [z for z in za.lesen() if z["name"] == "NPM_TOKEN"] == []


def test_an_empty_register_stays_quiet_and_says_why(capsys):
    calls = []
    JETZT = dt.datetime(2027, 1, 31, 9, tzinfo=dt.timezone.utc)
    assert za.main([], jetzt=JETZT, send=lambda t, **kw: calls.append(t) or True) == 0
    assert calls == [] and "keine Zugaenge" in capsys.readouterr().out


def test_the_example_register_carries_the_expiry(register):
    z = za.lesen(register[1])
    assert len(z) == 1 and z[0]["ablauf"] == dt.date(2027, 2, 7)


def test_the_register_holds_no_secret():
    text = (ROOT / "config" / "zugaenge.yaml").read_text()
    assert "npm_" not in text and "_authToken" not in text


def test_seven_days_before_sends_one_message(register):
    calls = []
    JETZT = dt.datetime(2027, 1, 31, 9, tzinfo=dt.timezone.utc)
    rc = za.main(register, jetzt=JETZT, send=lambda t, **kw: calls.append((t, kw)) or True)
    assert rc == 0 and len(calls) == 1
    text, kw = calls[0]
    assert kw == {"channel": za.notify.ALERTS, "art": "zugang-abgelaufen"}
    assert text.startswith("NPM_TOKEN läuft in 7 Tagen ab (07.02.2027)")
    assert len([z for z in text.splitlines() if z.strip()]) <= 4
    assert "Console-Tab" in text


def test_the_day_of_expiry_sends_again(register):
    calls = []
    JETZT = dt.datetime(2027, 2, 7, 9, tzinfo=dt.timezone.utc)
    za.main(register, jetzt=JETZT, send=lambda t, **kw: calls.append(t) or True)
    assert len(calls) == 1 and "läuft heute ab" in calls[0]


def test_the_days_around_stay_quiet(register):
    for tag in (dt.datetime(2026, 11, 9, 9), dt.datetime(2027, 1, 30, 9),
                dt.datetime(2027, 2, 1, 9), dt.datetime(2027, 2, 6, 9), dt.datetime(2027, 2, 8, 9)):
        calls = []
        assert za.main(register, jetzt=tag.replace(tzinfo=dt.timezone.utc), send=lambda t, **kw: calls.append(t) or True) == 0
        assert calls == [], tag


def test_a_probe_date_never_sends(register, capsys):
    calls = []
    assert za.main(["--heute", "2027-01-31", *register], send=lambda t, **kw: calls.append(t) or True) == 0
    assert calls == [] and "würde melden" in capsys.readouterr().out


def test_an_undelivered_warning_ends_with_the_mute_code(register):
    JETZT = dt.datetime(2027, 1, 31, 9, tzinfo=dt.timezone.utc)
    assert za.main(register, jetzt=JETZT, send=lambda t, **kw: False) == za.notify.MUTE_EXIT_CODE

