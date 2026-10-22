"""scripts/deploy_match.py: catch-up for a lost push event, alert after 30 min.

The decision is tested on check() with fixed times; main() is tested with the
network and the checkout replaced, so nothing reaches GitHub or Telegram.
"""
import datetime as dt
import importlib.util
import pathlib

import pytest

WURZEL = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "deploy_match", WURZEL / "scripts" / "deploy_match.py")
dm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dm)

HEAD = "a" * 40
OLD = "b" * 40
MERGED = dt.datetime(2026, 10, 21, 13, 54, tzinfo=dt.timezone.utc)


def at(minutes):
    return MERGED + dt.timedelta(minutes=minutes)


def test_server_on_main_is_silent():
    dev, actions, state = dm.check(at(500), HEAD, MERGED, HEAD, 0, {}, act=True)
    assert (dev, actions, state) == (0, [], {})


def test_young_head_without_run_waits():
    dev, actions, _ = dm.check(at(10), HEAD, MERGED, OLD, 0, {}, act=True)
    assert (dev, actions) == (0, [])


def test_head_without_run_after_15_min_is_dispatched_once():
    dev, actions, state = dm.check(at(16), HEAD, MERGED, OLD, 0, {}, act=True)
    assert (dev, actions) == (0, ["dispatch"])
    dev, actions, _ = dm.check(at(20), HEAD, MERGED, OLD, 1, state, act=True)
    assert actions == []


def test_head_with_a_run_is_never_dispatched():
    # A failed deploy rolled back for a reason; repeating it is not ours.
    _, actions, _ = dm.check(at(20), HEAD, MERGED, OLD, 1, {}, act=True)
    assert actions == []


def test_deviation_after_30_min_alerts_once_per_head():
    dev, actions, state = dm.check(at(31), HEAD, MERGED, OLD, 1, {}, act=True)
    assert (dev, actions) == (1, ["alert"])
    dev, actions, state = dm.check(at(90), HEAD, MERGED, OLD, 1, state, act=True)
    assert (dev, actions) == (1, [])
    newer = "c" * 40
    _, actions, _ = dm.check(at(120), newer, at(60), OLD, 1, state, act=True)
    assert actions == ["alert"]


def test_30_minutes_exactly_is_not_yet_a_deviation():
    dev, _, _ = dm.check(at(30), HEAD, MERGED, OLD, 1, {}, act=True)
    assert dev == 0


def test_count_is_read_only():
    state = {"head": OLD}
    dev, actions, new = dm.check(at(45), HEAD, MERGED, OLD, 0, state, act=False)
    assert (dev, actions, new) == (1, [], state)


def test_alert_text_says_what_to_do():
    t = dm.alert_text(OLD, HEAD, 47, 0, "14:09")
    assert "bbbbbbb" in t and "aaaaaaa" in t and "47 Minuten" in t
    assert "Run workflow" in t and "14:09" in t
    assert "Deploy-Lauf dazu, aber" in dm.alert_text(OLD, HEAD, 47, 1, None)


@pytest.fixture
def offline(monkeypatch, tmp_path):
    calls = {"dispatch": 0, "sent": []}
    monkeypatch.setattr(dm.gh, "token", lambda: "t")
    monkeypatch.setattr(dm, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(dm, "main_head", lambda tok: (HEAD, at(0)))
    monkeypatch.setattr(dm, "deploy_runs", lambda sha, tok: 0)
    monkeypatch.setattr(dm, "dispatch",
                        lambda tok: calls.__setitem__("dispatch", calls["dispatch"] + 1))
    monkeypatch.setattr(dm.notify, "send_telegram",
                        lambda text, channel: calls["sent"].append(text) or True)
    monkeypatch.setattr(dm, "_now", lambda: at(45))
    return calls


def test_main_count_sends_and_dispatches_nothing(offline, monkeypatch, capsys):
    monkeypatch.setattr(dm, "server_head", lambda: OLD)
    assert dm.main(["--count"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "1"
    assert offline == {"dispatch": 0, "sent": []}
    assert not dm.STATE.exists()


def test_main_cron_dispatches_and_alerts_then_stays_quiet(offline, monkeypatch, capsys):
    monkeypatch.setattr(dm, "server_head", lambda: OLD)
    dm.main([])
    assert offline["dispatch"] == 1 and len(offline["sent"]) == 1
    dm.main([])
    assert offline["dispatch"] == 1 and len(offline["sent"]) == 1


def test_main_on_main_prints_zero(offline, monkeypatch, capsys):
    monkeypatch.setattr(dm, "server_head", lambda: HEAD)
    dm.main([])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "0"
    assert offline == {"dispatch": 0, "sent": []}


def test_unreadable_without_token(monkeypatch, capsys):
    monkeypatch.setattr(dm.gh, "token", lambda: "")
    assert dm.main(["--count"]) == 2
    assert capsys.readouterr().out.startswith("UNREADABLE")
