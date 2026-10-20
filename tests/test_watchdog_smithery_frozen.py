"""Smithery stops tracking the origin; only the entry's loss alarms (ops SMITHERY-EXIT).

Smithery refuses new servers since 2026-10-19 ("Updating servers has been
removed"). An existing listing can be re-published until 2026-10-31 and stays
frozen after that. Each test pins one edge: before the date any count is quiet
and says a re-publish is still possible, from the date it reads "frozen", a gone
or emptied entry alarms on either side, and a registry outage is a skip.
"""
import datetime

import httpx
import pytest

from agents import watchdog as wd

UTC = datetime.timezone.utc
BEFORE = datetime.datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
FROM = datetime.datetime(2026, 10, 31, 0, 30, tzinfo=UTC)


PROXY = "https://moltrust-mcp-server--moltrust.run.tools"
DIRECT = "https://api.moltrust.ch/mcp"


def _resp(status, tools=None, url=PROXY):
    req = httpx.Request("GET", wd.SMITHERY_REGISTRY_URL)
    body = {"tools": [{"name": f"t{i}"} for i in range(tools)], "deploymentUrl": url,
            "connections": [{"type": "http", "deploymentUrl": url}]} if tools is not None else {}
    return httpx.Response(status, json=body, request=req)


@pytest.fixture
def registry(monkeypatch, tmp_path):
    state = {"resp": _resp(200, 56), "live": 61, "sent": [], "deliver": True}
    monkeypatch.setattr(wd, "SMITHERY_NOTICE_FILE", str(tmp_path / "notice.json"))

    def send(text, *, channel, **k):
        state["sent"].append((channel, text))
        return state["deliver"]

    monkeypatch.setattr(wd.notify, "send_telegram", send)
    monkeypatch.setattr(wd, "_live_mcp_tool_count", lambda: state["live"])

    def get(url, **k):
        if "smithery" not in url:
            return _resp(200, 0)
        if isinstance(state["resp"], Exception):
            raise state["resp"]
        return state["resp"]

    monkeypatch.setattr(wd.httpx, "get", get)
    monkeypatch.setattr(wd, "_check_x402_discovery", lambda: {"surface": "x402", "ok": True})
    monkeypatch.setattr(wd, "_is_weekly_slot", lambda now, day: False)
    return state


def _smithery(now):
    return next(r for r in wd.check_discovery_drift(now) if r["surface"] == "MCP↔Smithery")


def test_the_freeze_date_is_31_october():
    assert wd.SMITHERY_FROZEN_FROM == datetime.date(2026, 10, 31)


@pytest.mark.parametrize("listed", [56, 61])
def test_before_the_date_any_count_is_quiet(registry, listed):
    registry["resp"] = _resp(200, listed)
    r = _smithery(BEFORE)
    assert r["ok"] is True and r["frozen"] is False
    assert r["detail"] == (f"Smithery lists {listed} tools, origin 61; re-publish "
                           "possible until 2026-10-31, frozen after")


@pytest.mark.parametrize("listed,live", [(61, 61), (56, 61), (61, 64)])
def test_from_the_date_the_count_reads_frozen(registry, listed, live):
    registry.update(resp=_resp(200, listed), live=live)
    r = _smithery(FROM)
    assert r["ok"] is True and r["frozen"] is True
    assert r["detail"].startswith(f"Smithery frozen at {listed} tools since 2026-10-31")
    assert r["detail"].endswith(f"origin {live}")


@pytest.mark.parametrize("now", [BEFORE, FROM])
@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_entry_alarms(registry, now, status):
    registry["resp"] = _resp(status)
    r = _smithery(now)
    assert r["ok"] is False and f"HTTP {status}" in r["detail"]


@pytest.mark.parametrize("now", [BEFORE, FROM])
def test_an_emptied_entry_alarms(registry, now):
    registry["resp"] = _resp(200, 0)
    assert _smithery(now)["ok"] is False


@pytest.mark.parametrize("failure", [_resp(503, 0), httpx.ConnectTimeout("t")])
def test_a_registry_outage_is_a_skip(registry, failure):
    registry["resp"] = failure
    r = _smithery(FROM)
    assert r["ok"] is True and r["unverifiable"] is True


def test_the_old_rescan_annotation_is_gone():
    assert not hasattr(wd, "SMITHERY_RESCAN_PENDING")
    assert not hasattr(wd, "SMITHERY_FROZEN_AT")


def test_the_proxy_listing_at_56_says_nothing(registry):
    _smithery(BEFORE)
    _smithery(FROM)
    assert registry["sent"] == []


@pytest.mark.parametrize("now", [BEFORE, FROM])
@pytest.mark.parametrize("listed,live", [(61, 61), (61, 64), (64, 64)])
def test_a_rescan_above_56_is_announced_once(registry, now, listed, live):
    registry.update(resp=_resp(200, listed), live=live)
    _smithery(now)
    _smithery(now)
    assert len(registry["sent"]) == 1
    channel, text = registry["sent"][0]
    assert channel == wd.notify.WORKLOG
    assert f"lists {listed} tools (origin {live})" in text and "points at" not in text


@pytest.mark.parametrize("listed", [55, 56])
def test_56_or_fewer_on_the_proxy_is_no_rescan(registry, listed):
    registry.update(resp=_resp(200, listed), live=64)
    _smithery(BEFORE)
    assert registry["sent"] == []


def test_the_direct_url_is_announced_once_and_separately(registry):
    registry["resp"] = _resp(200, 61)
    _smithery(BEFORE)
    registry["resp"] = _resp(200, 61, url=DIRECT)
    _smithery(BEFORE)
    _smithery(BEFORE)
    assert [t for _, t in registry["sent"]][1:] == [
        "Smithery took the rescan: @moltrust/moltrust-mcp-server now points at "
        "https://api.moltrust.ch/mcp."]
    assert len(registry["sent"]) == 2


def test_both_at_once_are_one_message(registry):
    registry["resp"] = _resp(200, 61, url=DIRECT)
    _smithery(BEFORE)
    assert len(registry["sent"]) == 1
    assert "lists 61 tools" in registry["sent"][0][1] and DIRECT in registry["sent"][0][1]


def test_a_failed_send_is_retried_next_run(registry):
    registry.update(resp=_resp(200, 61), deliver=False)
    _smithery(BEFORE)
    registry["deliver"] = True
    _smithery(BEFORE)
    _smithery(BEFORE)
    assert len(registry["sent"]) == 2


def test_a_gone_entry_announces_nothing(registry):
    registry["resp"] = _resp(404)
    _smithery(BEFORE)
    assert registry["sent"] == []
