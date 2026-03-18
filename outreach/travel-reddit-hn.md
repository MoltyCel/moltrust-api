# Reddit/HN Drafts -- MT Travel Developer Guide

## r/LocalLLaMA

**Title:** We built W3C delegation chains for AI travel booking agents -- 10-step verification, 6 endpoints, 3 MCP tools

**Body:**

Building trust infrastructure for autonomous agents. One vertical: travel booking.

The problem: an AI booking agent operating on behalf of a company has no enforceable constraints. It can upgrade to Business Class, book a 5-star hotel when the policy says 3-star, or exceed the daily travel budget. Without a delegation chain, the agent's mandate is just a prompt -- not a cryptographic constraint.

We built **MT Travel** -- a booking verification API using W3C Verifiable Credentials with delegation chains.

**The delegation chain:**

```
Company (Principal)
  └→ Travel Agency (Delegatee) -- spend limit: $5,000/trip
       └→ Booking Platform (Sub-Delegatee) -- cabin: economy only
```

Each level of the chain has its own constraints: spend limits, cabin class, hotel star rating, currency, allowed segments (hotel, flight, car rental). The constraints are enforced cryptographically, not by prompt engineering.

**Issue a TravelAgentCredential:**

```python
import requests

vc = requests.post(
    "https://api.moltrust.ch/guard/vc/travel-agent/issue",
    json={
        "agentDid": "did:moltrust:booking-agent-001",
        "principalDid": "did:moltrust:acme-corp",
        "spendLimit": 5000,
        "currency": "USDC",
        "segments": ["flight", "hotel", "car_rental"],
        "constraints": {
            "cabinClass": "economy",
            "hotelStars": 3,
            "maxNights": 5
        },
        "validDays": 90
    }
).json()
```

**Verify a booking (10-step pipeline):**

```python
receipt = requests.post(
    "https://api.moltrust.ch/guard/travel/verify",
    json={
        "vc": travel_agent_credential,
        "booking": {
            "segment": "flight",
            "amount": 850,
            "currency": "USDC",
            "cabinClass": "economy",
            "merchant": "united.com"
        }
    }
).json()

# receipt["result"] → "approved" / "review" / "rejected"
# receipt["receiptId"] → on-chain booking receipt
```

The 10-step pipeline checks: VC signature (Ed25519), expiry, DID resolution, segment authorization, spend limit, currency match, daily cap, trust score, delegation chain validity, and traveler manifest.

**6 endpoints, 3 MCP tools:**

| Endpoint | Method | Auth | Cost |
|----------|--------|------|------|
| `/travel/info` | GET | Public | Free |
| `/travel/schema` | GET | Public | Free |
| `/travel/receipt/:id` | GET | Public | Free |
| `/travel/trip/:tripId` | GET | Public | Free |
| `/travel/verify` | POST | EA free | Free (EA) |
| `/vc/travel-agent/issue` | POST | EA free | Free (EA) |

MCP tools: `mt_travel_info`, `mt_travel_verify`, `mt_travel_issue_vc` (`pip install moltrust-mcp-server`, 36 tools total).

Developer guide: https://moltrust.ch/blog/travel-developer-guide.html
Travel info: https://api.moltrust.ch/guard/travel/info

---

## Show HN

**Title:** Show HN: W3C delegation chains for AI travel booking agents -- 10-step verify pipeline

**URL:** https://moltrust.ch/blog/travel-developer-guide.html

**Body:**

MT Travel verifies autonomous travel booking agent transactions using TravelAgentCredentials (W3C VCs, Ed25519 JWS) with delegation chains.

The delegation chain models real-world travel authority: Company issues a credential to Travel Agency with a $5,000 spend limit and economy-only cabin constraint. Travel Agency delegates to a Booking Platform with further restrictions. Each level of the chain is cryptographically enforced -- not prompt-based.

The verify endpoint runs a 10-step pipeline: VC signature, expiry, DID resolution, segment check (flight/hotel/car), spend limit, currency match, daily cap, trust score, delegation chain validation, and traveler manifest binding. Returns a receipt with approval status and on-chain audit trail.

Use case: corporate travel where an AI agent books flights and hotels within policy constraints, with every booking receipt anchored on Base L2 for compliance.

Stack: Node.js/Hono, Ed25519 JWS, W3C VCs with delegation chains, Base L2 anchoring.

6 endpoints, 3 MCP tools. Free during Early Access.

Developer guide: https://moltrust.ch/blog/travel-developer-guide.html
Travel info: https://api.moltrust.ch/guard/travel/info
