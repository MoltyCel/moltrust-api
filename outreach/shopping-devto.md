---
title: "How to Verify AI Shopping Agents with W3C Credentials"
published: true
tags: [ai, webdev, blockchain, mcp]
canonical_url: https://moltrust.ch/blog/shopping-developer-guide.html
cover_image: https://moltrust.ch/img/og/og-blog.png?v=2
---

Autonomous shopping agents will process $25 billion in transactions by 2028. Without cryptographic identity verification at checkout, every agent purchase is a trust-me-bro handshake with no accountability.

MT Shopping is a transaction verification API for autonomous shopping agents. It issues and verifies **BuyerAgentCredentials** — W3C Verifiable Credentials (VCs) signed with Ed25519 — that bind an AI agent to its human principal's spending constraints. 5 REST endpoints, 3 Model Context Protocol (MCP) tools, free during Early Access. Part of [MolTrust](https://moltrust.ch) v0.7.0.

## What Is a BuyerAgentCredential?

A BuyerAgentCredential is a W3C Verifiable Credential that authorizes an AI shopping agent to make purchases on behalf of a human. It contains the agent's DID, the human principal's DID, spend limits, allowed currencies, category restrictions, and an expiration date. The credential is signed with Ed25519 and can be verified by any merchant in a single API call.

```json
{
  "type": ["VerifiableCredential", "BuyerAgentCredential"],
  "credentialSubject": {
    "id": "did:moltrust:agent-001",
    "humanDID": "did:moltrust:human-001",
    "authorization": {
      "spendLimit": 300,
      "currency": "USDC",
      "validFrom": "2026-03-16T00:00:00Z",
      "validUntil": "2026-04-16T00:00:00Z",
      "scope": {
        "categories": ["electronics", "books"],
        "maxTransactionsPerDay": 5
      }
    }
  }
}
```

Unlike API-key-only authorization, a BuyerAgentCredential is portable, cryptographically verifiable, and carries its own constraints. The merchant doesn't need to trust the agent — it trusts the credential.

## Verify a Purchase: One POST Request

The verification endpoint runs a 10-step pipeline: credential type, Ed25519 signature, expiry, valid-from, spend limit, currency, daily cap, trust score, DID resolution, and merchant scope. It returns a receipt with an approval status.

```python
import requests

receipt = requests.post(
    "https://api.moltrust.ch/guard/shopping/verify",
    json={
        "agentDID": "did:moltrust:agent-001",
        "vc": buyer_agent_credential,  # Full W3C VC object
        "merchant": "amazon.com",
        "amount": 189.99,
        "currency": "USDC"
    }
).json()

print(receipt["result"])     # "approved", "review", or "rejected"
print(receipt["guardScore"]) # 0-100 trust score
print(receipt["receiptId"])  # Unique receipt ID for audit trail
```

Response:

```json
{
  "receiptId": "r-a1b2c3d4-e5f6-7890",
  "agentDID": "did:moltrust:agent-001",
  "humanDID": "did:moltrust:human-001",
  "merchant": "amazon.com",
  "amount": 189.99,
  "currency": "USDC",
  "guardScore": 72,
  "result": "approved",
  "reason": "All checks passed",
  "timestamp": "2026-03-16T10:30:00Z"
}
```

**Trust scores below 20 are rejected. Scores 20-49 are flagged for review. 50+ approved.** The merchant uses `guardScore` and `result` to gate or escalate purchases automatically.

## Issue a Credential

Issue a BuyerAgentCredential via the x402 payment protocol. $5 USDC on Base L2 — free during Early Access.

```python
vc = requests.post(
    "https://api.moltrust.ch/guard/vc/buyer-agent/issue",
    json={
        "agentDID": "did:moltrust:agent-001",
        "humanDID": "did:moltrust:human-001",
        "spendLimit": 500,
        "currency": "USDC",
        "categories": ["electronics", "clothing"],
        "maxTransactionsPerDay": 10,
        "validDays": 30
    }
).json()

print(vc["credential"])       # Signed BuyerAgentCredential (JWS)
print(vc["credentialHash"])   # SHA-256 hash
```

## The 5 Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/guard/shopping/info` | GET | Free | Service info and schema reference |
| `/guard/shopping/schema` | GET | Free | Full BuyerAgentCredential JSON schema |
| `/guard/shopping/verify` | POST | Free (EA) | Verify agent purchase against credential |
| `/guard/vc/buyer-agent/issue` | POST | $5 USDC | Issue a signed BuyerAgentCredential |
| `/guard/shopping/receipt/:id` | GET | Free | Retrieve verification receipt |

## MCP Integration

MT Shopping is available as 3 MCP tools in the `moltrust-mcp-server` package (33 tools total, v0.7.0):

```bash
# Install
pip install moltrust-mcp-server

# Add to Claude Desktop / Cursor / Windsurf
claude mcp add moltrust -- uvx moltrust-mcp-server

# Available tools:
# mt_shopping_info     — service info and schema
# mt_shopping_verify   — verify agent purchase
# mt_shopping_issue_vc — issue BuyerAgentCredential
```

Any MCP-compatible client can verify shopping transactions natively without writing HTTP calls.

## Use Cases

### Merchant-Side Verification

An e-commerce platform receives a purchase request from an AI agent. Before processing payment, the platform calls `/shopping/verify` with the agent's credential and transaction details. If the receipt says "rejected", the order is blocked. If "review", a human is notified. If "approved", the order proceeds — with a cryptographic audit trail.

### Agent-Side Credential Management

A shopping agent framework (AutoGPT, CrewAI, or custom) requests a BuyerAgentCredential from its human principal's MolTrust account. The credential is scoped to specific categories, spending limits, and time windows. The agent presents this credential at every checkout — no stored API keys, no shared passwords.

## FAQ

**What is MT Shopping?**
A transaction verification API for autonomous shopping agents. Issues BuyerAgentCredentials (W3C VCs) binding an AI agent to human spending constraints, with a single API call for merchant verification.

**How does the verification pipeline work?**
10-step check: credential type, Ed25519 signature, expiry, valid-from, spend limit, currency match, daily cap, trust score, DID resolution, merchant scope. Returns "approved", "review", or "rejected".

**What does it cost?**
Verification and info endpoints are free permanently. Credential issuance is $5 USDC via x402 on Base L2. All free during Early Access.

**Can I use it with Claude Desktop?**
Yes. `pip install moltrust-mcp-server`, add to Claude Desktop, use `mt_shopping_verify` natively.

---

5 endpoints. 3 MCP tools. W3C Verifiable Credentials. Free during Early Access.

[API Docs](https://api.moltrust.ch/guard) | [PyPI](https://pypi.org/project/moltrust-mcp-server/) | [@MolTrust](https://x.com/MolTrust)
