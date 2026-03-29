"""MolTrust MCP Server - Trust Layer tools for AI assistants"""

from mcp.server.fastmcp import FastMCP
import httpx

API_URL = "https://api.moltrust.ch"
import os
API_KEY = os.environ.get("MOLTRUST_API_KEY", "")

mcp = FastMCP("MolTrust", instructions="Trust Layer for the Agent Economy. Verify agent identities, check reputation, issue and verify W3C Verifiable Credentials.")


def _api(method, path, json=None):
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    if method == "GET":
        r = httpx.get(API_URL + path, headers=headers, timeout=10)
    else:
        r = httpx.post(API_URL + path, headers=headers, json=json, timeout=10)
    return r.json()


@mcp.tool()
def register_agent(display_name: str, platform: str = "moltrust") -> dict:
    """Register a new AI agent and get a W3C DID identity."""
    return _api("POST", "/identity/register", {"display_name": display_name, "platform": platform})


@mcp.tool()
def verify_agent(did: str) -> dict:
    """Check if an agent DID is registered and verified."""
    return _api("GET", "/identity/verify/" + did)


@mcp.tool()
def resolve_did(did: str) -> dict:
    """Resolve a DID to its document (supports did:moltrust and did:web)."""
    return _api("GET", "/identity/resolve/" + did)


@mcp.tool()
def get_reputation(did: str) -> dict:
    """Get an agent's trust score and total ratings."""
    return _api("GET", "/reputation/query/" + did)


@mcp.tool()
def rate_agent(from_did: str, to_did: str, score: int) -> dict:
    """Rate another agent (1-5). Builds the trust network."""
    return _api("POST", "/reputation/rate", {"from_did": from_did, "to_did": to_did, "score": score})


@mcp.tool()
def issue_credential(subject_did: str, credential_type: str = "AgentTrustCredential") -> dict:
    """Issue a W3C Verifiable Credential signed with Ed25519."""
    return _api("POST", "/credentials/issue", {"subject_did": subject_did, "credential_type": credential_type})


@mcp.tool()
def verify_credential(credential: dict) -> dict:
    """Verify a Verifiable Credential's Ed25519 signature and validity."""
    return _api("POST", "/credentials/verify", {"credential": credential})


@mcp.tool()
def get_did_document() -> dict:
    """Get MolTrust's own W3C DID:web document."""
    return _api("GET", "/.well-known/did.json")


@mcp.tool()
def health_check() -> dict:
    """Check MolTrust API status."""
    return _api("GET", "/health")


if __name__ == "__main__":
    mcp.run(transport="stdio")
