#!/usr/bin/env python3
"""Write the MCP Server Card from the live tools/list (ops SMITHERY-EXIT, 20.10.2026).

Crawlers and registries (SmitheryBot, Glama, AgenstryBot, ...) read
/.well-known/mcp/server-card.json on api.moltrust.ch. Until 2026-10-20 the card
came from moltrust-web and carried "tools": "dynamic" — no tool list at all —
and the package version 1.2.4. This script asks the running MCP server what it
serves and writes that, so the card names exactly the tools a client gets: 61
on 2026-10-20, the package's 48 plus the molcheck_*, moltproof_* and ads_* tools
that services/mcp_http.py adds.

deploy.sh runs it after every moltrust-api deploy. It never writes a card it
could not fill: no answer, an error or an empty list leaves the previous card
in place and exits 1.

    python3 scripts/gen_mcp_server_card.py [--url URL] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
from importlib import metadata

DEFAULT_URL = "http://127.0.0.1:8002/mcp"
DEFAULT_OUT = os.path.expanduser("~/moltstack/data/mcp-server-card.json")
PACKAGE = "moltrust-mcp-server"

STATIC = {
    "description": ("Give an agent a verifiable identity, and check another agent's before "
                    "dealing with it. Includes MolCheck verdicts on committed agent mandates "
                    "and allow/hold/forbid verdicts for Amazon Ads agents."),
    "homepage": "https://moltrust.ch",
    "documentationUrl": "https://moltrust.ch/for-agents.html",
    "repository": "https://github.com/MoltyCel/moltrust-mcp-server",
    "transport": {"type": "streamable-http", "endpoint": "https://api.moltrust.ch/mcp"},
    "authentication": {
        "required": False,
        "schemes": [{"type": "apiKey", "in": "header", "name": "X-API-Key"}],
    },
    "provider": {"name": "CryptoKRI GmbH", "url": "https://moltrust.ch"},
}
SERVER_NAME = "io.github.MoltyCel/moltrust-mcp-server"
SERVER_TITLE = "MolTrust MCP Server"
TOOL_KEYS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")


def _rpc(url: str, body: dict) -> dict | None:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "User-Agent": "moltrust-server-card/1"})
    with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 - fixed local/https URL
        raw = r.read().decode()
    # Streamable HTTP answers as SSE; the JSON is on the data: line.
    for line in raw.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            break
    return json.loads(raw) if raw.strip().startswith("{") else None


def live_tools(url: str) -> list[dict]:
    _rpc(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "server-card", "version": "1"}}})
    tools, cursor = [], None
    for _ in range(20):  # the server does not paginate today; a cursor loop costs nothing
        params = {"cursor": cursor} if cursor else {}
        data = _rpc(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": params})
        if not data or "result" not in data:
            raise RuntimeError(f"tools/list answered without a result: {str(data)[:200]}")
        tools += data["result"].get("tools", [])
        cursor = data["result"].get("nextCursor")
        if not cursor:
            return tools
    raise RuntimeError("tools/list kept returning a cursor after 20 pages")


def package_version() -> str:
    try:
        return metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:
        return "unknown"


def build(tools: list[dict], version: str) -> dict:
    if not tools:
        raise RuntimeError("tools/list returned no tools")
    names = [t.get("name") for t in tools]
    if len(set(names)) != len(names) or not all(names):
        raise RuntimeError("tools/list returned a missing or duplicate name")
    for t in tools:
        if not isinstance(t.get("inputSchema"), dict):
            raise RuntimeError(f"tool {t.get('name')} has no inputSchema")
    card = {"serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": version}}
    card.update(STATIC)
    card["tools"] = [{k: t[k] for k in TOOL_KEYS if k in t} for t in sorted(tools, key=lambda t: t["name"])]
    return card


def write_atomic(path: str, card: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".server-card.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(card, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    card, err = None, None
    for attempt in range(3):  # the MCP unit may still be starting after a restart
        try:
            card = build(live_tools(a.url), package_version())
            break
        except OSError as e:
            err = e
            time.sleep(3)
        except Exception as e:  # a bad answer does not get better by waiting
            err = e
            break
    if card is None:  # keep the previous card; say why
        print(f"server card NOT written: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    write_atomic(a.out, card)
    print(f"server card written: {len(card['tools'])} tools, version {card['serverInfo']['version']}, {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
