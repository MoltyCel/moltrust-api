"""A known, outstanding Smithery re-scan is annotated, not alarmed (ops MC-API).

#843 took the hosted MCP endpoint from 56 to 61 tools. Smithery re-scans only
from Lars' login, so until then MCP↔Smithery would alarm on every run about a
state everyone knows. SMITHERY_RESCAN_PENDING annotates exactly that pair until
a date. Each test here pins one edge: the pair is quiet, any other pair alarms,
the date ends the annotation, and a synced listing is plain green.
"""
import datetime

import pytest

from agents import watchdog as wd

UTC = datetime.timezone.utc
PENDING = wd.SMITHERY_RESCAN_PENDING


class _Resp:
    def __init__(self, n):
        self._n = n

    def json(self):
        return {"tools": [{"name": f"t{i}"} for i in range(self._n)]}


@pytest.fixture
def counts(monkeypatch):
    state = {"live": PENDING["live"], "listed": PENDING["listed"]}
    monkeypatch.setattr(wd, "_live_mcp_tool_count", lambda: state["live"])
    monkeypatch.setattr(wd.httpx, "get", lambda url, **k:
                        _Resp(state["listed"]) if "smithery" in url else _Resp(0))
    monkeypatch.setattr(wd, "_check_x402_discovery", lambda: {"surface": "x402", "ok": True})
    monkeypatch.setattr(wd, "_is_weekly_slot", lambda now, day: False)
    return state


def _smithery(now):
    return next(r for r in wd.check_discovery_drift(now) if r["surface"] == "MCP↔Smithery")


def _day(d):
    return datetime.datetime.combine(d, datetime.time(12), tzinfo=UTC)


def test_the_known_pair_is_annotated_not_alarmed(counts):
    r = _smithery(_day(PENDING["until"]))
    assert r["ok"] is True and r["annotated"] is True
    assert r["detail"].startswith("56 listed / 61 live expected")


def test_the_day_after_the_date_alarms_again(counts):
    r = _smithery(_day(PENDING["until"] + datetime.timedelta(days=1)))
    assert r["ok"] is False and "annotated" not in r


@pytest.mark.parametrize("live,listed", [(62, 56), (61, 55), (60, 56), (56, 61)])
def test_any_other_pair_alarms(counts, live, listed):
    counts.update(live=live, listed=listed)
    r = _smithery(_day(PENDING["until"]))
    assert r["ok"] is False and "annotated" not in r


def test_a_synced_listing_is_plain_green(counts):
    counts.update(live=61, listed=61)
    r = _smithery(_day(PENDING["until"]))
    assert r["ok"] is True and "annotated" not in r
    assert "in sync" in r["detail"]


def test_the_annotation_cannot_be_open_ended():
    assert isinstance(PENDING["until"], datetime.date)
    assert PENDING["until"] - datetime.date.fromisoformat(PENDING["since"]) <= datetime.timedelta(days=14)
