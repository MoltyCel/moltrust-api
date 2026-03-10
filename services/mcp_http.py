#!/usr/bin/env python3
"""MolTrust MCP Server — HTTP Streamable Transport.

Runs the same MCP server (with all tools) over HTTP instead of stdio.
Deployed behind nginx at https://api.moltrust.ch/mcp

Includes MoltGuard integrity tools (7 additional tools).
"""

import os
import sys

# Use local REST API to avoid round-tripping through nginx
os.environ.setdefault("MOLTRUST_API_URL", "http://127.0.0.1:8000")

from moltrust_mcp_server.server import mcp  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

# Register MoltGuard tools
sys.path.insert(0, os.path.dirname(__file__))
from moltguard_mcp_tools import register_moltguard_tools  # noqa: E402
register_moltguard_tools(mcp)

# Override settings for HTTP deployment behind nginx
mcp.settings.host = "127.0.0.1"
mcp.settings.port = 8002
mcp.settings.streamable_http_path = "/mcp"

# Allow nginx-proxied requests (default DNS rebinding protection
# only allows localhost origins, but nginx sends Host: api.moltrust.ch)
mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "api.moltrust.ch"],
    allowed_origins=[
        "http://127.0.0.1:*",
        "http://localhost:*",
        "https://api.moltrust.ch",
        "https://smithery.ai",
        "https://server.smithery.ai",
    ],
)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
