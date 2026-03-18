---
title: "How to Prevent AI Fantasy Sports Agents from Backdating Lineups"
published: true
tags: [ai, sports, blockchain, webdev]
canonical_url: https://moltrust.ch/blog/fantasy-sports-developer-guide.html
cover_image: https://moltrust.ch/img/og/og-blog.png?v=2
---

Your AI fantasy agent cherry-picks lineups after kickoff. Here's how to prove it didn't. SHA-256 commitment hashes anchored on Base L2 before the contest starts -- 4 endpoints, 5 platforms, 8 sport types.

MT Fantasy Sports is a lineup commitment and verification API for autonomous fantasy draft agents. Agents commit lineups before contests start using SHA-256 hashes anchored on Base L2, then settle with actual scores, ranks, and prize outcomes. 4 REST endpoints across 5 platforms (DraftKings, FanDuel, Yahoo, Sleeper, custom) and 8 sport types. Free during Early Access.

**Important:** This vertical uses SHA-256 commitment anchoring, not W3C Verifiable Credentials. Fantasy lineups are high-frequency, short-lived objects -- a full VC envelope would add overhead without meaningful benefit. A VC wrapper is planned for a future release.

## The Backdating Problem

AI fantasy agents are proliferating across DraftKings, FanDuel, and Yahoo. An agent claims it drafted Mahomes, Kelce, and Hill before the injury report dropped -- but did it? Without a pre-contest commitment, any track record is retroactive storytelling.

The fix is a commitment hash. Before the contest starts, the agent submits its full lineup to the API. The API hashes it, anchors the hash on Base L2, and returns a commitment that anyone can verify.

## How the Commitment Schema Works

The commitment is a two-step SHA-256 hash:

1. **lineup_hash** = `SHA-256(canonical JSON of lineup)` -- fingerprints the exact player selections
2. **commitment_hash** = `SHA-256(agent_did:contest_id:lineup_hash:timestamp)` -- anchored on Base L2

Anyone can independently recompute both hashes from the original payload.

## Commit a Lineup

```python
import requests

result = requests.post(
    "https://api.moltrust.ch/sports/fantasy/lineups/commit",
    headers={"X-API-Key": "your_api_key"},
    json={
        "agent_did": "did:moltrust:a1b2c3d4e5f67890",
        "contest_id": "dk-nfl-sun-main-2026w12",
        "platform": "draftkings",
        "sport": "nfl",
        "contest_type": "classic",
        "contest_start_iso": "2026-11-22T13:00:00Z",
        "entry_fee_usd": 20.0,
        "lineup": {
            "QB": "Patrick Mahomes",
            "RB1": "Derrick Henry",
            "RB2": "Josh Jacobs",
            "WR1": "Tyreek Hill",
            "WR2": "CeeDee Lamb",
            "WR3": "Amon-Ra St. Brown",
            "TE": "Travis Kelce",
            "FLEX": "Saquon Barkley",
            "DST": "San Francisco 49ers"
        },
        "projected_score": 178.5,
        "confidence": 0.68
    }
).json()

print(result["commitment_hash"])  # 64-char SHA-256 hex
print(result["lineup_hash"])       # SHA-256 of lineup object
print(result["verify_url"])        # Public verification URL
```

Response:

```json
{
  "commitment_hash": "a3f8c1...",
  "timestamp_iso": "2026-11-22T10:15:00Z",
  "tx_hash": "0x...",
  "chain": "base",
  "agent_did": "did:moltrust:a1b2c3d4e5f67890",
  "contest_id": "dk-nfl-sun-main-2026w12",
  "lineup_hash": "b7e2d9...",
  "status": "committed",
  "verify_url": "https://api.moltrust.ch/sports/fantasy/lineups/verify/a3f8c1..."
}
```

## Verify a Lineup

Public endpoint -- no API key needed. Shows the full lineup, timing proof, and on-chain status.

```python
proof = requests.get(
    "https://api.moltrust.ch/sports/fantasy/lineups/verify/a3f8c1..."
).json()

print(proof["minutes_before_contest"])  # 165 (2h 45m before kickoff)
print(proof["on_chain"]["verified"])     # true
print(proof["result"]["actual_score"])   # 192.3
print(proof["result"]["rank"])           # 47
```

## Settle with Results

After the contest, settle with actual outcome data:

```python
requests.patch(
    "https://api.moltrust.ch/sports/fantasy/lineups/settle/a3f8c1...",
    headers={"X-API-Key": "your_api_key"},
    json={
        "actual_score": 192.3,
        "rank": 47,
        "total_entries": 12500,
        "prize_usd": 85.0,
        "percentile": 99.6
    }
)
```

## Track Record

The history endpoint returns aggregated stats:

```python
stats = requests.get(
    "https://api.moltrust.ch/sports/fantasy/history/did:moltrust:a1b2c3d4e5f67890",
    headers={"X-API-Key": "your_api_key"}
).json()

print(stats["fantasy_stats"]["itm_rate"])              # 0.42
print(stats["fantasy_stats"]["roi"])                   # 0.18
print(stats["fantasy_stats"]["projection_accuracy"])  # 0.91
```

Stats breakdown:

- **itm_rate** -- In-the-money rate: percentage of settled lineups that won a prize
- **roi** -- Return on investment: `(total_prizes - total_fees) / total_fees`
- **projection_accuracy** -- How close projected scores match actual: `1 - |projected - actual| / projected`

## The 4 Endpoints

| Endpoint | Method | Auth | Cost |
|----------|--------|------|------|
| `/sports/fantasy/lineups/commit` | POST | API Key | 1 credit |
| `/sports/fantasy/lineups/verify/:hash` | GET | Public | Free |
| `/sports/fantasy/lineups/settle/:hash` | PATCH | API Key | Free |
| `/sports/fantasy/history/:did` | GET | API Key | 1 credit |

Platforms: DraftKings, FanDuel, Yahoo, Sleeper, custom. Sports: NFL, NBA, MLB, NHL, PGA, NASCAR, soccer, custom.

## MCP Tools

MT Fantasy Sports does not have dedicated MCP tools yet. Integration is via REST API only. MCP tools are planned for v0.8.0.

## FAQ

**Why not W3C Verifiable Credentials?**
Fantasy lineups are high-frequency, short-lived objects. SHA-256 commitment hashing provides the same tamper-proof guarantee with less complexity. A VC wrapper is planned for a future release.

**What's the difference to Signal Provider?**
Signal Provider tracks prediction accuracy (home_win/away_win/draw) with auto-settlement via API-Football. Fantasy tracks full lineup compositions with manual settlement and ROI/ITM stats.

**How is the commitment hash computed?**
Two-step SHA-256: `lineup_hash = SHA-256(canonical JSON of lineup)`, then `commitment_hash = SHA-256(agent_did:contest_id:lineup_hash:timestamp)`. Anchored on Base L2.

**What does it cost?**
Commit and history cost 1 credit each. Verify and settle are free. All free during Early Access.

---

4 endpoints. SHA-256 commitment hashes. Base L2 anchoring. Free during Early Access.

[MolTrust Sports](https://moltrust.ch/sports.html) | [API Health](https://api.moltrust.ch/sports/health) | [@MolTrust](https://x.com/MolTrust)
