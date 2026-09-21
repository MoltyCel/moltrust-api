"""The weekly checks fire once a week, and the Glama check asks the right question.

Two defects, both found on 2026-09-21 and both of the same family: a check that
looks like it is working while it is not.

`GLAMA_CHECK_WEEKDAY = 0` was read as "on Mondays". The watchdog runs hourly, so
that meant twenty-four identical alerts every Monday — the Glama drift was
reported once an hour from midnight. The anchor-proof replay and the agent-card
signature check carried the same shape.

The Glama check itself asked whether the origin's tool count appears anywhere on
the listing page. The page is 790 KB: neighbouring servers in the sidebar, each
with a count, plus our own description text saying "53 tools across 12 areas".
When that description reached the page the check went green on a coincidence.

And the comparison underneath it was wrong in the first place. Glama indexes the
GitHub repository, which declares the package's 48 tools — name for name the 48
Glama shows. The origin exposes 53 because `services/mcp_http.py` adds five
moltproof_* tools that live in this repository and ship to nobody. Smithery
lists the remote at origin and says 53; Glama lists the package and says 48;
both are right about different questions. Comparing them raised a drift alarm
for an architecture decision and advised a re-index that would have changed
nothing.
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

PACKAGED = 48   # what the published moltrust-mcp-server declares
HOSTED = 53     # the package plus the five moltproof_* tools mcp_http adds

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


def _serving(page: str):
    return lambda *a, **k: type("R", (), {"text": page})()


def test_the_listing_matching_the_package_is_quiet(monkeypatch):
    """The live state. Glama indexes the GitHub repository, which declares the
    package's 48 tools — name for name the 48 Glama shows. The hosted endpoint
    exposing 53 is not drift, it is mcp_http adding five moltproof_* tools that
    ship to nobody."""
    monkeypatch.setattr("agents.watchdog.httpx.get", _serving(_page(PACKAGED)))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: PACKAGED)
    result = _check_glama(HOSTED)
    assert result["ok"] is True
    assert "package == listing" in result["detail"]
    assert "adds 5 more" in result["detail"]


def test_the_hosted_count_alone_never_decides(monkeypatch):
    """Comparing Glama against the origin is what produced a drift alarm for an
    architecture decision, and advised a re-index that would have changed
    nothing. The origin count may appear in the message; it may not decide it."""
    monkeypatch.setattr("agents.watchdog.httpx.get", _serving(_page(PACKAGED)))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: PACKAGED)
    for hosted in (PACKAGED, HOSTED, HOSTED + 20):
        assert _check_glama(hosted)["ok"] is True, hosted


def test_drift_is_reported_when_the_listing_lags_the_package(monkeypatch):
    """The real staleness case: the package gained a tool, Glama has not
    re-crawled the repository yet."""
    monkeypatch.setattr("agents.watchdog.httpx.get", _serving(_page(PACKAGED)))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: PACKAGED + 1)
    result = _check_glama(HOSTED)
    assert result["ok"] is False
    assert "has not re-crawled" in result["detail"]


def test_our_own_description_text_does_not_make_the_check_green(monkeypatch):
    """The 2026-09-21 false green: the page carries our own copy saying
    "53 tools across 12 areas", and the old check asked only whether that
    number appeared anywhere on the page."""
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        _serving(_page(PACKAGED, stray_counts=(53, 3, 7, 11, 19))))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: PACKAGED + 1)
    result = _check_glama(HOSTED)
    assert result["ok"] is False, "a count from our own description went green"


def test_an_unimportable_package_is_a_skip_not_drift(monkeypatch):
    monkeypatch.setattr("agents.watchdog.httpx.get", _serving(_page(PACKAGED)))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: None)
    result = _check_glama(HOSTED)
    assert result["ok"] is True
    assert "skipped" in result["detail"]


def test_an_unparseable_page_is_a_skip_not_drift(monkeypatch):
    """A layout change on their side is not our listing going stale."""
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        lambda *a, **k: type("R", (), {"text": "<html></html>"})())
    result = _check_glama(HOSTED)
    assert result["ok"] is True
    assert "skipped" in result["detail"]


def test_glama_being_down_is_a_skip_not_drift(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("nope")
    monkeypatch.setattr("agents.watchdog.httpx.get", boom)
    result = _check_glama(HOSTED)
    assert result["ok"] is True
    assert "unreachable" in result["detail"]
