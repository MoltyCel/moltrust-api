"""Dispatch-level auth gate for MCP tools/call requests.

Runs as a pure-ASGI middleware between FastAPI's identity_middleware (which
sets request.state.identity) and the mounted MCP sub-app at /mcp. Inspects
the JSON-RPC envelope, extracts the tool name on tools/call requests, looks
up the required identity tier in TOOL_AUTH_MATRIX, and rejects with a
JSON-RPC error envelope if the resolved identity is insufficient.

Non-/mcp paths and non-POST methods pass through without parsing. Malformed
bodies pass through so the MCP transport can produce its own standard error.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp_auth_matrix import identity_satisfies, required_tier

_GATE_PATH_PREFIX = "/mcp"
_RPC_INSUFFICIENT_IDENTITY = -32001
_RPC_INVALID_REQUEST = -32600
# 1 MB cap on buffered request bodies. MCP tool calls in the wild fit
# comfortably under 100 KB even with verbose JSON arguments; anything
# significantly larger is either a misuse or an explicit attack on the
# in-memory accumulation in _buffer_body. Reject early with 413.
_MAX_BODY_BYTES = 1024 * 1024


class _BodyTooLarge(Exception):
    def __init__(self, bytes_seen: int) -> None:
        self.bytes_seen = bytes_seen


class McpAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").startswith(_GATE_PATH_PREFIX)
        ):
            await self.app(scope, receive, send)
            return

        try:
            body = await _buffer_body(receive)
        except _BodyTooLarge as exc:
            await _send_oversized_error(send, exc.bytes_seen)
            return

        try:
            envelope = json.loads(body) if body else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            await _forward_with_body(self.app, scope, body, send)
            return

        if envelope is None:
            await _forward_with_body(self.app, scope, body, send)
            return

        tool_names = _extract_tool_call_names(envelope)
        if not tool_names:
            await _forward_with_body(self.app, scope, body, send)
            return

        identity = _read_identity_from_scope(scope)
        kind = getattr(identity, "kind", None)

        for tool in tool_names:
            required = required_tier(tool)
            if not identity_satisfies(kind, required):
                await _send_jsonrpc_error(
                    send,
                    rpc_id=_extract_id_for_tool(envelope, tool),
                    tool=tool,
                    required=required,
                    actual=kind,
                )
                return

        await _forward_with_body(self.app, scope, body, send)


def _read_identity_from_scope(scope: Scope):
    """Read the identity that identity_middleware wrote via
    `request.state.identity = ...`. Going through Starlette's own
    Request.state wrapper makes us version-agnostic — Starlette knows
    whether scope["state"] is a plain dict or a State proxy."""
    from starlette.requests import Request as _Req
    request = _Req(scope)
    return getattr(request.state, "identity", None)


async def _buffer_body(
    receive: Receive, max_bytes: int = _MAX_BODY_BYTES
) -> bytes:
    """Drain the receive channel of all http.request messages and return
    the full body as a single bytes object. Aborts with _BodyTooLarge if
    the cumulative chunk size exceeds max_bytes — caller is expected to
    respond with HTTP 413 and a JSON-RPC error envelope without invoking
    the downstream app."""
    chunks: list[bytes] = []
    total = 0
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            break
        chunk = msg.get("body", b"")
        total += len(chunk)
        if total > max_bytes:
            raise _BodyTooLarge(total)
        chunks.append(chunk)
        if not msg.get("more_body", False):
            break
    return b"".join(chunks)


async def _send_oversized_error(send: Send, bytes_seen: int) -> None:
    """HTTP 413 + JSON-RPC error envelope for over-cap bodies. The body
    can't be parsed (we aborted mid-read), so the rpc id is null."""
    payload = {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": _RPC_INVALID_REQUEST,
            "message": (
                f"Request body exceeds {_MAX_BODY_BYTES} byte cap "
                f"(received at least {bytes_seen} bytes before abort)."
            ),
            "data": {"max_bytes": _MAX_BODY_BYTES, "bytes_seen": bytes_seen},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _forward_with_body(
    app: ASGIApp, scope: Scope, body: bytes, send: Send
) -> None:
    """Invoke the downstream app with a receive callable that yields the
    buffered body once and then http.disconnect."""
    sent = False

    async def replay_receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    await app(scope, replay_receive, send)


async def _send_jsonrpc_error(
    send: Send,
    *,
    rpc_id: Any,
    tool: str,
    required: str,
    actual: str | None,
) -> None:
    kind_display = actual if actual is not None else "unauthenticated"
    payload = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": _RPC_INSUFFICIENT_IDENTITY,
            "message": (
                f"Tool '{tool}' requires identity tier '{required}'; "
                f"current identity is '{kind_display}'."
            ),
            "data": {
                "tool": tool,
                "required": required,
                "actual": kind_display,
                "claim_url": "https://api.moltrust.ch/auth/claim",
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _extract_tool_call_names(envelope: Any) -> list[str]:
    """Return every tool name appearing in tools/call methods of this
    envelope. Handles batch arrays (JSON-RPC 2.0 allows them). Returns
    an empty list if the envelope contains no tools/call methods."""
    if isinstance(envelope, list):
        out: list[str] = []
        for sub in envelope:
            out.extend(_extract_tool_call_names(sub))
        return out
    if not isinstance(envelope, dict):
        return []
    if envelope.get("method") != "tools/call":
        return []
    name = (envelope.get("params") or {}).get("name")
    return [name] if isinstance(name, str) and name else []


def _extract_id_for_tool(envelope: Any, tool: str) -> Any:
    """Find the JSON-RPC id of the tools/call request for the given tool
    name so the error envelope can echo it back to the caller."""
    if isinstance(envelope, dict):
        if (
            envelope.get("method") == "tools/call"
            and (envelope.get("params") or {}).get("name") == tool
        ):
            return envelope.get("id")
    elif isinstance(envelope, list):
        for sub in envelope:
            if (
                isinstance(sub, dict)
                and sub.get("method") == "tools/call"
                and (sub.get("params") or {}).get("name") == tool
            ):
                return sub.get("id")
    return None
