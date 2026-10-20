"""The Smithery entry is frozen; only its disappearance alarms (ops SMITHERY-EXIT).

Since 2026-10-19 Smithery refuses every create or update ("Updating servers has
been removed"). The entry stays at the 56 tools it scanned on 2026-10-12 while
the origin serves 61. Each test pins one edge: the frozen count is quiet, a
different count is still quiet but named, a gone or emptied entry alarms, and a
registry outage is a skip, never a finding.
"""
import datetime

import httpx
import pytest

from agents import watchdog as wd

NOW = datetime.datetime(2026, 10, 21, 12, 0, tzinfo=datetime.timezone.utc)


def _resp(status, tools=None):
    req = httpx.Request("GET", wd.SMITHERY_REGISTRY_URL)
    body = {"tools": [{"name": f"t{i}"} for i in range(tools)]} if tools is not None else {}
    return httpx.Response(status, json=body, request=req)


@pytest.fixture
def registry(monkeypatch):
    state = {"resp": _resp(200, 56), "live": 61}
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


def _smithery():
    return next(r for r in wd.check_discovery_drift(NOW) if r["surface"] == "MCP↔Smithery")


def test_the_frozen_count_is_quiet(registry):
    r = _smithery()
    assert r["ok"] is True and r["frozen"] is True
    assert r["detail"].startswith("Smithery frozen at 56 tools since 2026-10-20")
    assert "origin 61" in r["detail"] and "changed" not in r["detail"]


@pytest.mark.parametrize("listed,live", [(56, 62), (56, 48), (55, 61), (61, 61)])
def test_any_count_stays_quiet(registry, listed, live):
    registry.update(resp=_resp(200, listed), live=live)
    r = _smithery()
    assert r["ok"] is True
    assert ("changed from 56" in r["detail"]) is (listed != 56)


@pytest.mark.parametrize("status", [404, 410])
def test_a_gone_entry_alarms(registry, status):
    registry["resp"] = _resp(status)
    r = _smithery()
    assert r["ok"] is False and f"HTTP {status}" in r["detail"]


def test_an_emptied_entry_alarms(registry):
    registry["resp"] = _resp(200, 0)
    assert _smithery()["ok"] is False


@pytest.mark.parametrize("failure", [_resp(503, 0), httpx.ConnectTimeout("t")])
def test_a_registry_outage_is_a_skip(registry, failure):
    registry["resp"] = failure
    r = _smithery()
    assert r["ok"] is True and r["unverifiable"] is True


def test_the_old_rescan_annotation_is_gone():
    assert not hasattr(wd, "SMITHERY_RESCAN_PENDING")
