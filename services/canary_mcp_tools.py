"""MolTrust leak canary MCP tools (ops LC-1): canary_issue, canary_list, canary_events.

Pass-through to the REST endpoints under /canary on the local API, with the
caller's own API key from the MCP connection. Nothing is stored here, and the
key goes nowhere but the X-API-Key header of that one request.
"""

import json
import os
from typing import Optional

import httpx

try:
    from mcp.server.fastmcp import Context
except ImportError:  # mcp 2.x renamed FastMCP; the hosted server runs 1.x (services/mcp_http.py)
    Context = object

API_URL = os.environ.get("MOLTRUST_API_URL", "http://127.0.0.1:8000")
TIMEOUT = 15.0
# Tests route requests into the app in-process; production leaves this None.
_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None

_KEY_REQUIRED = json.dumps({
    "error": "api_key_required",
    "detail": "Send your MolTrust API key as X-API-Key on the MCP connection. "
              "Free key: POST https://api.moltrust.ch/auth/signup.",
})


def _request_api_key(ctx) -> Optional[str]:
    req = getattr(getattr(ctx, "request_context", None), "request", None) if ctx else None
    if req is None:
        return None
    key = req.headers.get("x-api-key")
    if not key:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    return key or None


async def _call(method: str, path: str, key: str, *, params: Optional[dict] = None,
                body: Optional[dict] = None) -> str:
    async with httpx.AsyncClient(base_url=API_URL, timeout=TIMEOUT, transport=_TRANSPORT) as client:
        try:
            r = await client.request(method, path, params=params, json=body,
                                     headers={"X-API-Key": key})
        except httpx.HTTPError as e:
            return json.dumps({"error": "api_unreachable", "detail": type(e).__name__})
    try:
        payload = r.json()
    except ValueError:
        payload = {"detail": r.text[:500]}
    if r.status_code >= 400:
        return json.dumps({"error": "api_error", "status": r.status_code, "detail": payload})
    return json.dumps(payload, indent=2)


def register_canary_tools(mcp) -> None:
    """Register canary_issue, canary_list and canary_events on the shared server."""

    @mcp.tool()
    async def canary_issue(counterparty_did: str, mode: str = "bound", on_leak: str = "hold",
                           credential_type: str = "AgentTrustCredential",
                           ctx: Optional[Context] = None) -> str:
        """Issue a leak canary for one counterparty: a credential to hand to that counterparty.
        If anyone else presents it at the MolTrust verifier, you get a signed leak event that
        names the counterparty. Costs what a credential costs. Needs your MolTrust API key as
        the X-API-Key header of the MCP connection.

        Args:
            counterparty_did: DID of the agent you hand the credential to
            mode: "bound" (issued to the counterparty's DID) or "decoy" (issued to a DID
                  nobody holds; any presentation but yours is a leak)
            on_leak: "hold" puts the counterparty on hold in your mandates
                     (POST /canary/enforce-hook), "none" only records the event
            credential_type: the credential type, as in POST /credentials/issue
        """
        key = _request_api_key(ctx)
        if not key:
            return _KEY_REQUIRED
        return await _call("POST", "/canary/issue", key, body={
            "counterparty_did": counterparty_did, "mode": mode, "on_leak": on_leak,
            "credential_type": credential_type})

    @mcp.tool()
    async def canary_list(limit: int = 50, offset: int = 0,
                          ctx: Optional[Context] = None) -> str:
        """Your leak canaries, newest first: counterparty, mode, reaction on leak and the number
        of events so far. Paginated with limit/offset; `next_offset` is null on the last page.
        Needs your MolTrust API key as X-API-Key on the MCP connection.
        """
        key = _request_api_key(ctx)
        if not key:
            return _KEY_REQUIRED
        return await _call("GET", "/canary/list", key, params={"limit": limit, "offset": offset})

    @mcp.tool()
    async def canary_events(canary_id: Optional[str] = None, limit: int = 50, offset: int = 0,
                            ctx: Optional[Context] = None) -> str:
        """Your leak events, newest first, each signed by MolTrust and carrying AER material
        that `moltrust-verify` recomputes offline (trust list: GET /canary/trust-list).
        Optionally for one canary. Needs your MolTrust API key as X-API-Key on the MCP
        connection.

        Args:
            canary_id: only events of this canary (from canary_issue or canary_list)
            limit: page size, at most 200
            offset: where the page starts
        """
        key = _request_api_key(ctx)
        if not key:
            return _KEY_REQUIRED
        params = {"limit": limit, "offset": offset}
        if canary_id:
            params["canary_id"] = canary_id
        return await _call("GET", "/canary/events", key, params=params)
