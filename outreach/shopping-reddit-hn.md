# Reddit/HN Drafts — MT Shopping Developer Guide

## r/LocalLLaMA

**Title:** We built W3C credential verification for AI shopping agents — here's the API

**Body:**

We're building trust infrastructure for autonomous agents. One vertical: shopping.

The problem: when an AI agent tries to buy something on behalf of a user, merchants have no way to verify (a) the agent is authorized, (b) what its spending limits are, or (c) who's liable if something goes wrong.

We built **MT Shopping** — a verification API that issues and checks **BuyerAgentCredentials** (W3C Verifiable Credentials, Ed25519 signatures).

**How it works:**

```python
import requests

receipt = requests.post(
    "https://api.moltrust.ch/guard/shopping/verify",
    json={
        "agentDID": "did:moltrust:agent-001",
        "vc": credential,  # W3C VC with spend limits, categories, expiry
        "merchant": "amazon.com",
        "amount": 189.99,
        "currency": "USDC"
    }
).json()

# receipt["result"] → "approved" / "review" / "rejected"
# receipt["guardScore"] → 0-100 trust score
```

The verify endpoint runs 10 checks: signature, expiry, spend limit, currency, daily cap, trust score, DID resolution, etc.

Also available as MCP tools:

```bash
pip install moltrust-mcp-server
claude mcp add moltrust -- uvx moltrust-mcp-server
# mt_shopping_verify, mt_shopping_info, mt_shopping_issue_vc
```

5 endpoints, 3 MCP tools, free during Early Access.

Developer guide: https://moltrust.ch/blog/shopping-developer-guide.html
API docs: https://api.moltrust.ch/guard/api/info

Happy to answer questions about the protocol design.

---

## Show HN

**Title:** Show HN: MolTrust Shopping — verify AI agent identities before checkout ($5 USDC)

**Body:**

MT Shopping verifies autonomous shopping agent transactions using W3C Verifiable Credentials.

A merchant receives a purchase request from an AI agent. Before processing, they POST to `/guard/shopping/verify` with the agent's BuyerAgentCredential. The API checks the Ed25519 signature, spend limits, currency, expiry, daily cap, and trust score. Returns `approved`, `review`, or `rejected` with a receipt ID.

The credential binds the agent to its human principal's constraints: max spend, allowed categories, transaction limits, time windows. Portable across merchants — the agent carries the credential, not a per-merchant API key.

Stack: Node.js/Hono backend, Ed25519 JWS signatures, Base L2 anchoring, x402 payment protocol. Also available as MCP tools (`pip install moltrust-mcp-server`, 33 tools total).

5 endpoints. Verification is free permanently. Credential issuance: $5 USDC via x402 on Base (free during Early Access).

Developer guide: https://moltrust.ch/blog/shopping-developer-guide.html
API: https://api.moltrust.ch/guard/shopping/info
Source (MCP server): https://github.com/MoltyCel/moltrust-mcp-server
