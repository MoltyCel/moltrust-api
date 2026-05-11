"""Unit tests for the MCP tool authorization matrix and dispatch gate.

Covers:
- Every tool registered with the running FastMCP server has a matrix entry
  (defense against silently allowing a new tool to inherit the fail-closed
  "claimed" default by accident).
- identity_satisfies returns the right verdict for every (kind, required)
  pair including None / unknown kinds.
- The dispatch middleware buffers and replays the body, gates tools/call,
  and lets initialize / notifications / tools/list pass through.
"""

from __future__ import annotations

import json

import pytest

from app.mcp_auth_matrix import (
    DEFAULT_REQUIREMENT,
    TOOL_AUTH_MATRIX,
    identity_satisfies,
    required_tier,
)


# --- Matrix coverage ----------------------------------------------------------

def test_matrix_covers_every_registered_tool():
    """Every tool the running server registers must have a matrix entry.

    Imports app.main (which mounts the MCP server and registers all tools)
    and asserts each tool name appears in TOOL_AUTH_MATRIX. New tools that
    forget a matrix entry fall through to DEFAULT_REQUIREMENT='claimed' —
    correct fail-closed behavior, but a developer mistake to surface.
    """
    import app.main  # noqa: F401  — mount + register tools as side effect
    from app.main import _moltrust_mcp

    registered = set(_moltrust_mcp._tool_manager._tools.keys())
    missing = registered - set(TOOL_AUTH_MATRIX.keys())
    assert not missing, (
        f"{len(missing)} registered tool(s) missing from TOOL_AUTH_MATRIX "
        f"(would fail-closed to '{DEFAULT_REQUIREMENT}'): {sorted(missing)}"
    )


def test_matrix_has_no_unknown_tiers():
    valid = {"any", "probe", "claimed"}
    for tool, tier in TOOL_AUTH_MATRIX.items():
        assert tier in valid, f"{tool} has unknown tier {tier!r}"


# --- identity_satisfies -------------------------------------------------------

@pytest.mark.parametrize(
    ("kind", "required", "expected"),
    [
        # any: always satisfied
        (None, "any", True),
        ("probe-new", "any", True),
        ("probe", "any", True),
        ("claimed", "any", True),
        ("api-key", "any", True),
        # probe: any identified caller
        (None, "probe", False),
        ("probe-new", "probe", True),
        ("probe", "probe", True),
        ("claimed", "probe", True),
        ("api-key", "probe", True),
        # claimed: only permanent identities
        (None, "claimed", False),
        ("probe-new", "claimed", False),
        ("probe", "claimed", False),
        ("claimed", "claimed", True),
        ("api-key", "claimed", True),
        # unknown kinds: fail closed
        ("garbage", "probe", False),
        ("garbage", "claimed", False),
        ("garbage", "any", True),  # any never blocks
    ],
)
def test_identity_satisfies(kind, required, expected):
    assert identity_satisfies(kind, required) is expected


def test_required_tier_default_for_unknown_tool():
    """Defense in depth: an unlisted tool returns the fail-closed default."""
    assert required_tier("nonexistent_tool_xyz") == DEFAULT_REQUIREMENT
    assert DEFAULT_REQUIREMENT == "claimed"


# --- Middleware integration ---------------------------------------------------

class _FakeIdentity:
    def __init__(self, kind: str | None) -> None:
        self.kind = kind


@pytest.mark.asyncio
async def test_middleware_passes_through_non_mcp_paths():
    from app.mcp_auth_middleware import McpAuthMiddleware

    forwarded: dict = {}

    async def downstream(scope, receive, send):
        forwarded["called"] = True
        forwarded["scope_path"] = scope["path"]

    mw = McpAuthMiddleware(downstream)

    async def receive():
        return {"type": "http.request", "body": b'{"method":"tools/call","params":{"name":"moltrust_claim_deposit"}}', "more_body": False}

    async def send(msg):  # not used on pass-through
        pass

    await mw({"type": "http", "method": "POST", "path": "/other", "state": {}}, receive, send)
    assert forwarded.get("called") is True


@pytest.mark.asyncio
async def test_middleware_rejects_claimed_tool_for_probe():
    from app.mcp_auth_middleware import McpAuthMiddleware

    downstream_called = {"val": False}

    async def downstream(scope, receive, send):
        downstream_called["val"] = True

    mw = McpAuthMiddleware(downstream)
    body = json.dumps({
        "jsonrpc": "2.0", "id": 42, "method": "tools/call",
        "params": {"name": "moltrust_claim_deposit", "arguments": {}},
    }).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    captured: list[dict] = []

    async def send(msg):
        captured.append(msg)

    await mw(
        {
            "type": "http", "method": "POST", "path": "/mcp/",
            "state": {"identity": _FakeIdentity("probe")},
        },
        receive, send,
    )

    assert downstream_called["val"] is False
    assert captured[0]["status"] == 200
    payload = json.loads(captured[1]["body"])
    assert payload["error"]["code"] == -32001
    assert payload["error"]["data"]["tool"] == "moltrust_claim_deposit"
    assert payload["error"]["data"]["required"] == "claimed"
    assert payload["error"]["data"]["actual"] == "probe"


@pytest.mark.asyncio
async def test_middleware_allows_probe_tool_for_probe():
    from app.mcp_auth_middleware import McpAuthMiddleware

    downstream_called = {"val": False}
    seen_body = {"val": b""}

    async def downstream(scope, receive, send):
        downstream_called["val"] = True
        msg = await receive()
        seen_body["val"] = msg["body"]

    mw = McpAuthMiddleware(downstream)
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "moltguard_score", "arguments": {"address": "0x1"}},
    }).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        pass

    await mw(
        {
            "type": "http", "method": "POST", "path": "/mcp/",
            "state": {"identity": _FakeIdentity("probe-new")},
        },
        receive, send,
    )

    assert downstream_called["val"] is True
    assert seen_body["val"] == body


@pytest.mark.asyncio
async def test_middleware_passes_initialize_through():
    """initialize is not tools/call, so the gate must not parse it as one."""
    from app.mcp_auth_middleware import McpAuthMiddleware

    downstream_called = {"val": False}

    async def downstream(scope, receive, send):
        downstream_called["val"] = True

    mw = McpAuthMiddleware(downstream)
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    }).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        pass

    await mw(
        {"type": "http", "method": "POST", "path": "/mcp/", "state": {}},
        receive, send,
    )

    assert downstream_called["val"] is True


@pytest.mark.asyncio
async def test_middleware_rejects_unknown_tool_as_claimed():
    """Fail-closed default: an unknown tool requires claimed identity."""
    from app.mcp_auth_middleware import McpAuthMiddleware

    downstream_called = {"val": False}

    async def downstream(scope, receive, send):
        downstream_called["val"] = True

    mw = McpAuthMiddleware(downstream)
    body = json.dumps({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "absolutely_not_a_real_tool", "arguments": {}},
    }).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    captured: list[dict] = []

    async def send(msg):
        captured.append(msg)

    await mw(
        {
            "type": "http", "method": "POST", "path": "/mcp/",
            "state": {"identity": _FakeIdentity("probe")},
        },
        receive, send,
    )

    assert downstream_called["val"] is False
    payload = json.loads(captured[1]["body"])
    assert payload["error"]["data"]["required"] == "claimed"
