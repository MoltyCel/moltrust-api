"""The MCP Server Card lists every tool the hosted endpoint serves (ops SMITHERY-EXIT).

Until 2026-10-20 the card said "tools": "dynamic". scripts/gen_mcp_server_card.py
writes it from the live tools/list after each deploy; the route prefers that
file and falls back to the moltrust-web card. Nothing here opens a socket.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

import app.main as main

_spec = importlib.util.spec_from_file_location(
    "gen_card", Path(__file__).resolve().parents[1] / "scripts" / "gen_mcp_server_card.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _tool(name, schema=True):
    t = {"name": name, "description": f"{name} does a thing", "extra": "dropped"}
    if schema:
        t["inputSchema"] = {"type": "object", "properties": {}}
    return t


def test_the_card_carries_every_tool_sorted_with_its_schema():
    card = gen.build([_tool("b_tool"), _tool("a_tool")], "1.2.5")
    assert [t["name"] for t in card["tools"]] == ["a_tool", "b_tool"]
    assert card["tools"][0]["inputSchema"] == {"type": "object", "properties": {}}
    assert "extra" not in card["tools"][0]
    assert card["serverInfo"] == {"name": gen.SERVER_NAME, "title": gen.SERVER_TITLE, "version": "1.2.5"}
    assert card["homepage"] == "https://moltrust.ch" and card["description"]
    assert card["transport"]["endpoint"] == "https://api.moltrust.ch/mcp"


@pytest.mark.parametrize("tools", [[], [_tool("a"), _tool("a")], [_tool("a", schema=False)],
                                   [{"description": "no name", "inputSchema": {}}]])
def test_a_card_that_cannot_be_filled_is_refused(tools):
    with pytest.raises(RuntimeError):
        gen.build(tools, "1.2.5")


def test_a_failed_run_keeps_the_previous_card(tmp_path, monkeypatch):
    out = tmp_path / "card.json"
    out.write_text('{"old": true}')
    monkeypatch.setattr(gen, "live_tools", lambda url: [])
    assert gen.main(["--out", str(out)]) == 1
    assert json.loads(out.read_text()) == {"old": True}


def test_a_good_run_writes_the_card(tmp_path, monkeypatch):
    out = tmp_path / "data" / "card.json"
    monkeypatch.setattr(gen, "live_tools", lambda url: [_tool(f"t{i}") for i in range(61)])
    assert gen.main(["--out", str(out)]) == 0
    assert len(json.loads(out.read_text())["tools"]) == 61


def test_the_route_prefers_the_generated_card(tmp_path):
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "server-card.json").write_text('{"tools": "dynamic"}')
    live = tmp_path / "live.json"
    live.write_text(json.dumps({"tools": [{"name": "x"}]}))
    with mock.patch.object(main, "_WEB_ROOT_WELL_KNOWN", str(tmp_path)), \
         mock.patch.object(main, "_MCP_SERVER_CARD", str(live)):
        resp = asyncio.run(main.well_known_mcp_server_card())
    assert json.loads(resp.body) == {"tools": [{"name": "x"}]}


def test_deploy_writes_the_card_after_the_api_deploy():
    sh = (Path(__file__).resolve().parents[1] / "ops" / "deploy" / "deploy.sh").read_text()
    body = sh[sh.index("deploy_api() {"):sh.index("\n}\n", sh.index("deploy_api() {"))]
    assert body.rstrip().endswith("write_server_card")
    assert "scripts/gen_mcp_server_card.py" in sh
