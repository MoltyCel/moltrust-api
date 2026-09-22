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
    MCP_REGISTRY_SERVER_NAME,
    PROOF_CHECK_WEEKDAY,
    REGISTRY_CHECK_WEEKDAY,
    WEEKLY_CHECK_HOUR,
    _check_glama,
    _is_weekly_slot,
    _version_key,
    check_registry_matches_pypi,
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
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: (PACKAGED, ""))
    result = _check_glama(HOSTED)
    assert result["ok"] is True
    assert "package == listing" in result["detail"]
    assert "adds 5 more" in result["detail"]


def test_the_hosted_count_alone_never_decides(monkeypatch):
    """Comparing Glama against the origin is what produced a drift alarm for an
    architecture decision, and advised a re-index that would have changed
    nothing. The origin count may appear in the message; it may not decide it."""
    monkeypatch.setattr("agents.watchdog.httpx.get", _serving(_page(PACKAGED)))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: (PACKAGED, ""))
    for hosted in (PACKAGED, HOSTED, HOSTED + 20):
        assert _check_glama(hosted)["ok"] is True, hosted


def test_drift_is_reported_when_the_listing_lags_the_package(monkeypatch):
    """The real staleness case: the package gained a tool, Glama has not
    re-crawled the repository yet."""
    monkeypatch.setattr("agents.watchdog.httpx.get", _serving(_page(PACKAGED)))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: (PACKAGED + 1, ""))
    result = _check_glama(HOSTED)
    assert result["ok"] is False
    assert "has not re-crawled" in result["detail"]


def test_our_own_description_text_does_not_make_the_check_green(monkeypatch):
    """The 2026-09-21 false green: the page carries our own copy saying
    "53 tools across 12 areas", and the old check asked only whether that
    number appeared anywhere on the page."""
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        _serving(_page(PACKAGED, stray_counts=(53, 3, 7, 11, 19))))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count", lambda: (PACKAGED + 1, ""))
    result = _check_glama(HOSTED)
    assert result["ok"] is False, "a count from our own description went green"


def test_an_unimportable_package_is_reported_not_skipped(monkeypatch):
    """This was a green skip until 2026-09-22, and that is the failure worth
    naming: Glama being unreachable is their side and passes, but the package
    not importing is ours and silences the comparison for good. Run from an
    interpreter without it installed, the whole check read as a success."""
    monkeypatch.setattr("agents.watchdog.httpx.get", _serving(_page(PACKAGED)))
    monkeypatch.setattr("agents.watchdog._package_mcp_tool_count",
                        lambda: (None, "ModuleNotFoundError: no module named ..."))
    result = _check_glama(HOSTED)
    assert result["ok"] is False
    assert "did not run" in result["detail"]
    assert "ModuleNotFoundError" in result["detail"], "the alert must name the cause"
    assert "pip install" in result["detail"], "and say what fixes it"


def test_the_real_counter_hands_back_a_reason_with_the_number():
    """The signature is a pair on purpose — a bare None told the alert nothing
    about why, and an alert that cannot name its cause gets ignored."""
    from agents.watchdog import _package_mcp_tool_count

    count, why = _package_mcp_tool_count()
    assert (count is None) == bool(why), (count, why)


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


# ---------------------------------------------------------------------------
# One package, two indices
# ---------------------------------------------------------------------------

def _indices(pypi: str, registry_versions, name=MCP_REGISTRY_SERVER_NAME):
    def get(url, *a, **k):
        if "pypi.org" in url:
            body = {"info": {"version": pypi}}
        else:
            body = {"servers": [{"server": {"name": name, "version": v}}
                                for v in registry_versions]}
        return type("R", (), {"json": lambda self: body})()
    return get


def test_both_indices_on_the_same_version_is_quiet(monkeypatch):
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        _indices("1.2.3", ["0.3.2", "0.6.0", "0.7.0", "1.2.3"]))
    result = check_registry_matches_pypi()
    assert result["ok"] is True
    assert "1.2.3" in result["detail"]


def test_a_registry_left_behind_is_reported(monkeypatch):
    """The 2026-09-22 state for two hours: the tag published the wheel and then
    failed on the registry's description cap."""
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        _indices("1.2.3", ["0.3.2", "0.6.0", "0.7.0"]))
    result = check_registry_matches_pypi()
    assert result["ok"] is False
    assert "PyPI has 1.2.3" in result["detail"]
    assert "registry has 0.7.0" in result["detail"]
    assert "workflow_dispatch" in result["detail"], "say how to fix it"


def test_a_missing_listing_is_reported(monkeypatch):
    monkeypatch.setattr("agents.watchdog.httpx.get", _indices("1.2.3", []))
    result = check_registry_matches_pypi()
    assert result["ok"] is False
    assert "not in the MCP registry" in result["detail"]


def test_somebody_elses_fork_does_not_answer_for_us(monkeypatch):
    """The search endpoint matches a substring, so a fork comes back in the
    same list. Taking the highest version in it would report a green on
    someone else's release."""
    monkeypatch.setattr("agents.watchdog.httpx.get",
                        _indices("1.2.3", ["9.9.9"], name="io.github.someone/moltrust-mcp-server"))
    result = check_registry_matches_pypi()
    assert result["ok"] is False
    assert "not in the MCP registry" in result["detail"]


def test_an_index_being_down_is_a_skip(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("nope")
    monkeypatch.setattr("agents.watchdog.httpx.get", boom)
    result = check_registry_matches_pypi()
    assert result["ok"] is True
    assert "skipped" in result["detail"]


def test_versions_order_numerically_not_alphabetically():
    """`max()` over strings puts 0.9.0 above 0.10.0, and the registry hands
    back every version it has ever held."""
    assert max(["0.9.0", "0.10.0"], key=_version_key) == "0.10.0"
    assert max(["0.7.0", "1.2.3"], key=_version_key) == "1.2.3"


def test_the_registry_check_fires_once_a_week():
    day = MONDAY + datetime.timedelta(days=(REGISTRY_CHECK_WEEKDAY - MONDAY.weekday()) % 7)
    hits = [h for h in range(24) if _is_weekly_slot(_at(day, h), REGISTRY_CHECK_WEEKDAY)]
    assert hits == [WEEKLY_CHECK_HOUR]
