"""The weekly checks fire once a week, and the Glama check asks the right question.

Two defects, both found on 2026-09-21 and both of the same family: a check that
looks like it is working while it is not.

`GLAMA_CHECK_WEEKDAY = 0` was read as "on Mondays". The watchdog runs hourly, so
that meant twenty-four identical alerts every Monday — the Glama drift was
reported once an hour from midnight. The anchor-proof replay and the agent-card
signature check carried the same shape.

The Glama check itself asked whether the origin's tool count appears anywhere on
the listing page. The page is 790 KB: it carries the neighbouring servers in the
sidebar, each with its own count, and it carries our own description text, which
says "53 tools across 12 areas". When that description reached the page the
check went green while Glama's own analysis still read "With 48 tools" — a
listing that had not re-crawled, reported as in sync.
"""

import datetime

from agents.watchdog import (
    CARD_CHECK_WEEKDAY,
    GLAMA_CHECK_WEEKDAY,
    PROOF_CHECK_WEEKDAY,
    WEEKLY_CHECK_HOUR,
    _check_glama,
    _is_weekly_slot,
)

MONDAY = datetime.date(2026, 9, 21)
SUNDAY = datetime.date(2026, 9, 20)


def _at(day: datetime.date, hour: int) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, hour)


def test_a_weekly_check_fires_in_exactly_one_hourly_run():
    hits = [h for h in range(24) if _is_weekly_slot(_at(MONDAY, h), GLAMA_CHECK_WEEKDAY)]
    assert hits == [WEEKLY_CHECK_HOUR]


def test_a_weekly_check_is_silent_on_the_other_six_days():
    for offset in range(1, 7):
        day = MONDAY + datetime.timedelta(days=offset)
        assert not any(_is_weekly_slot(_at(day, h), GLAMA_CHECK_WEEKDAY) for h in range(24))


def test_every_weekly_check_uses_the_same_guard():
    """All three were written separately and all three had the same bug."""
    for weekday in (GLAMA_CHECK_WEEKDAY, PROOF_CHECK_WEEKDAY, CARD_CHECK_WEEKDAY):
        day = MONDAY + datetime.timedelta(days=(weekday - MONDAY.weekday()) % 7)
        hits = [h for h in range(24) if _is_weekly_slot(_at(day, h), weekday)]
        assert hits == [WEEKLY_CHECK_HOUR], weekday


def test_the_proof_and_card_checks_run_on_sunday():
    assert PROOF_CHECK_WEEKDAY == SUNDAY.weekday()
    assert CARD_CHECK_WEEKDAY == SUNDAY.weekday()


# ---------------------------------------------------------------------------
# What the Glama check counts
# ---------------------------------------------------------------------------

def _page(indexed_tools: int, stray_counts=()) -> str:
    """A listing page carrying `indexed_tools` indexed tools, plus any number of
    unrelated "N tools" strings of the kind the real page is full of."""
    blocks = "".join(f"<div>tool_{i}Arguments</div><div>tool_{i}Output</div>"
                     for i in range(indexed_tools))
    strays = "".join(f"<span>{n} tools</span>" for n in stray_counts)
    return f"<html><body>{blocks}{strays}</body></html>"


def test_drift_is_reported_when_glama_indexed_fewer_tools(monkeypatch):
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        lambda *a, **k: type("R", (), {"text": _page(48)})())
    result = _check_glama(53)
    assert result["ok"] is False
    assert "indexed 48" in result["detail"]


def test_our_own_description_text_does_not_make_the_check_green(monkeypatch):
    """The exact 2026-09-21 false green: Glama indexed 48, and the page carried
    our own copy saying "53 tools across 12 areas"."""
    page = _page(48, stray_counts=(53, 3, 7, 11, 19))
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        lambda *a, **k: type("R", (), {"text": page})())
    result = _check_glama(53)
    assert result["ok"] is False, "a count from our own description went green"
    assert "indexed 48" in result["detail"]


def test_a_matching_crawl_is_quiet(monkeypatch):
    """What "Watchdog nach Bestätigung wieder still" has to mean: quiet because
    Glama re-crawled, not because some number matched."""
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        lambda *a, **k: type("R", (), {"text": _page(53, (48, 3, 7))})())
    result = _check_glama(53)
    assert result["ok"] is True
    assert "53 tools indexed" in result["detail"]


def test_an_unparseable_page_is_a_skip_not_drift(monkeypatch):
    """A layout change on their side is not our listing going stale."""
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        lambda *a, **k: type("R", (), {"text": "<html></html>"})())
    result = _check_glama(53)
    assert result["ok"] is True
    assert "skipped" in result["detail"]


def test_glama_being_down_is_a_skip_not_drift(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("nope")
    monkeypatch.setattr("agents.watchdog.httpx.get", boom)
    result = _check_glama(53)
    assert result["ok"] is True
    assert "unreachable" in result["detail"]
