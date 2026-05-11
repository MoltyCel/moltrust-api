#!/usr/bin/env python3
"""MolTrust MCP Server — HTTP Streamable Transport.

DEPRECATED standalone process. The same MCP server is now mounted as an
ASGI sub-app under the main FastAPI app at app/main.py. The mount puts
identity resolution and the dispatch-level auth gate on the same code
path as the REST API, removing the prior auth bypass where /mcp ran
outside the FastAPI middleware stack.

Removal plan: at Phase 8 deploy, nginx /mcp proxy_pass switches from
127.0.0.1:8002 to :8000, and moltrust-mcp-http.service is stopped and
disabled. This file is kept until that cutover so the existing systemd
unit keeps working during transition.
"""

import os
import sys

# Use local REST API to avoid round-tripping through nginx
os.environ.setdefault("MOLTRUST_API_URL", "http://127.0.0.1:8000")

from moltrust_mcp_server.server import mcp  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

# Register MoltGuard tools and Auto-Probe identity tool
sys.path.insert(0, os.path.dirname(__file__))
from moltguard_mcp_tools import register_moltguard_tools  # noqa: E402
from probe_mcp_tools import register_probe_tools  # noqa: E402
register_moltguard_tools(mcp)
register_probe_tools(mcp)

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
