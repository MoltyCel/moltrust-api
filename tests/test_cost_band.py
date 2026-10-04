"""The band, the ladder and the profile cap.

Raised 2026-10-04 after a week of measurement: seven of seven days over the old
$1.50 breaker, none under the old $0.80 target, mean $1.883, projected $56.49
against a $25 band. The old band was set on 2026-09-27 against a day that
itself cost $2.555 — below the operating cost from the hour it was written.
"""
import datetime
import importlib.util
import json
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("x_meter", ROOT / "agents" / "x_meter.py")
xm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xm)


def test_the_instructed_numbers_are_the_ones_in_the_code():
    assert xm.DAILY_BREAK_USD == 2.00
    assert xm.MONTHLY_TARGET_USD == 60.0
    assert xm.DAILY_PROFILE_READS == 33


def test_the_ladder_has_room_between_its_rungs():
    # $60 over 30 days is $2.00, so target and breaker would otherwise be the
    # same number and the warning would arrive with the stop.
    assert xm.DAILY_TARGET_USD < xm.DAILY_ALARM_USD < xm.DAILY_BREAK_USD


def test_the_yaml_and_the_module_agree():
    y = yaml.safe_load((ROOT / "config" / "expectations.yaml").read_text())
    b = y["budget"]
    assert b["daily_break_usd"] == xm.DAILY_BREAK_USD
    assert b["monthly_target_usd"] == xm.MONTHLY_TARGET_USD
    assert b["daily_target_usd"] == xm.DAILY_TARGET_USD
    assert b["daily_alarm_usd"] == xm.DAILY_ALARM_USD
    assert b["daily_profile_reads"] == xm.DAILY_PROFILE_READS


def ledger(tmp_path, rows):
    f = tmp_path / "x_meter.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(f)


def test_profiles_exhausted_counts_distinct_profiles(tmp_path, monkeypatch):
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    at = f"{day}T09:00:00+00:00"
    # 32 distinct profiles, each seen twice: still under the cap, because X
    # bills per distinct resource and so does the meter.
    rows = [{"at": at, "kind": "read", "source": "search",
             "posts": [], "users": [f"u{i}" for i in range(32)]}] * 2
    monkeypatch.setattr(xm, "LEDGER", ledger(tmp_path, rows))
    monkeypatch.setattr(xm, "ledger_path", lambda: xm.LEDGER, raising=False)
    assert xm.profiles_exhausted() is None

    rows.append({"at": at, "kind": "read", "source": "mention",
                 "posts": [], "users": ["u32"]})
    monkeypatch.setattr(xm, "LEDGER", ledger(tmp_path, rows))
    why = xm.profiles_exhausted()
    assert why and "33 distinct profiles" in why


def test_the_cap_is_not_a_second_breaker():
    # Dropping the expansion must not stop the search. The radar builds the
    # expansion through one helper and nothing else.
    src = (ROOT / "agents" / "reply_radar.py").read_text()
    # Two call sites, plus the definition.
    assert src.count("**_author_expansion()") == 2
    # Inside gather the expansion is only ever built through the helper.
    body = src.split("def gather(", 1)[1]
    assert '"expansions": "author_id"' not in body
    helper = src.split("def _author_expansion()", 1)[1].split("\ndef ", 1)[0]
    assert "return {}" in helper and "profiles_exhausted" in helper


def test_the_per_draft_lookup_stays_outside_the_cap():
    # target_ok buys one profile per draft and the username builds the URL
    # rule (h) is checked against. Saving that cent would put a wrong URL in
    # the scan, so the exemption is written down rather than left to be
    # rediscovered.
    src = (ROOT / "agents" / "reply_radar.py").read_text()
    fn = src.split("def target_ok(", 1)[1].split("\ndef ", 1)[0]
    assert "DAILY_PROFILE_READS" in fn
    assert "_author_expansion" not in fn
