"""MolTrust Auto-Probe MCP tools — adds moltrust_identity to the FastMCP instance.

Per docs/auto-probe-token-spec.md §4.4: returns the current MCP session's
identity (probe or claimed). On a fresh probe mint the raw probe key is
included so the caller can persist it as X-API-Key for subsequent calls.

This module deliberately calls the FastAPI /auth/identity endpoint **without**
an X-API-Key header. The MCP HTTP server's env key would otherwise resolve to
a shared claimed identity, defeating per-user probe accounting. Future per-
session key forwarding in moltrust_mcp_server would let us reuse the session
key here, but until that lands, keyless is the correct default for this tool.
"""
from __future__ import annotations

import os

import httpx

API_URL = os.environ.get("MOLTRUST_API_URL", "http://127.0.0.1:8000").rstrip("/")
TIMEOUT = 10.0


def register_probe_tools(mcp) -> None:
    """Attach the moltrust_identity tool to a FastMCP server instance."""

    @mcp.tool()
    async def moltrust_identity() -> str:
        """Return the current session's MolTrust identity (probe or claimed).

        First call without an API key mints a fresh probe DID with a 24h TTL
        and 50-call cap. The returned `probe_key` (mt_probe_...) can be passed
        as X-API-Key on subsequent calls so history accumulates on one DID.

        Probes have read access to all verticals, can rate (probe-flagged),
        can self-issue credentials, but cannot transfer credits, claim USDC
        deposits, issue credentials to other agents, or write to the on-chain
        registry. Claim via POST /auth/claim to remove these limits.
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(f"{API_URL}/auth/identity")
        except httpx.HTTPError as exc:
            return f"Error contacting MolTrust API: {exc}"

        if resp.status_code != 200:
            return f"Error {resp.status_code}: {resp.text}"

        data = resp.json()
        kind = data.get("kind", "unknown")
        lines = [f"DID:  {data.get('did', '?')}", f"Kind: {kind}"]

        if kind == "claimed":
            lines.append(data.get("status", "permanent identity"))
            return "\n".join(lines)

        lines.append(f"Expires:           {data.get('expires_at', '?')}")
        lines.append(f"Calls remaining:   {data.get('calls_remaining', '?')}")

        summary = data.get("summary") or {}
        if any(summary.values()):
            lines.append("")
            lines.append("Probe activity so far:")
            for key in ("tool_calls", "unique_tools", "verticals_touched", "credentials_received"):
                if summary.get(key):
                    lines.append(f"  {key.replace('_', ' ')}: {summary[key]}")

        if data.get("probe_key"):
            lines.append("")
            lines.append(f"Probe key: {data['probe_key']}")
            lines.append("  ↑ Store this and pass as X-API-Key for subsequent calls.")
            lines.append("    Otherwise each keyless call mints a fresh probe DID.")

        lines.append("")
        lines.append(data.get("claim_value", "Claim before TTL to keep your history."))
        lines.append("")
        lines.append(f"Claim: {data.get('claim_with', 'POST /auth/claim')}")
        return "\n".join(lines)
