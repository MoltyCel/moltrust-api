# Reddit/HN Drafts -- MT Fantasy Sports Developer Guide

## r/fantasyfootball

**Title:** We built cryptographic proof that your AI fantasy agent picked the lineup before kickoff

**Body:**

AI fantasy agents are getting good. Some claim 40%+ ITM rates on DraftKings GPPs. Problem: how do you know the agent actually picked that lineup before the injury report dropped?

We built **MT Fantasy Sports** -- a lineup commitment API that creates a SHA-256 hash of the full lineup, wraps it in a signed W3C Verifiable Credential (FantasyLineupCredential, Ed25519), and anchors it on Base L2 before the contest starts.

**How it works:**

```python
import requests

# Commit BEFORE kickoff
result = requests.post(
    "https://api.moltrust.ch/sports/fantasy/lineups/commit",
    headers={"X-API-Key": "your_key"},
    json={
        "agent_did": "did:moltrust:a1b2c3d4e5f67890",
        "contest_id": "dk-nfl-sun-main-2026w12",
        "platform": "draftkings",
        "sport": "nfl",
        "contest_start_iso": "2026-11-22T13:00:00Z",
        "lineup": {"QB": "Mahomes", "RB1": "Henry", ...},
        "projected_score": 178.5,
        "confidence": 0.68
    }
).json()

# result["commitment_hash"] -> SHA-256, anchored on Base L2
# result["credential"] -> signed FantasyLineupCredential (W3C VC)
# result["verify_url"] -> anyone can verify
```

After the contest, settle with actual results. The API computes ITM rate, ROI, and projection accuracy across all your committed lineups.

**Public verification (no auth):**

```bash
curl https://api.moltrust.ch/sports/fantasy/lineups/verify/a3f8c1...
# -> full lineup, minutes_before_contest, on-chain proof, credential, actual score
```

Each commitment returns a signed FantasyLineupCredential (W3C VC, Ed25519) with the lineup hash, commitment hash, and Base L2 anchor inside the credentialSubject. Same standard as all other MolTrust verticals.

4 endpoints. DraftKings, FanDuel, Yahoo, Sleeper. NFL, NBA, MLB, NHL, PGA, NASCAR, soccer. Free during Early Access.

Developer guide: https://moltrust.ch/blog/fantasy-sports-developer-guide.html

---

## r/LocalLLaMA

**Title:** We built a lineup commitment API for AI fantasy agents -- W3C VCs + SHA-256 hashes on Base L2 prove your agent didn't backdate

**Body:**

Building trust infrastructure for autonomous agents. Latest vertical: fantasy sports.

The problem: AI agents drafting lineups on DraftKings/FanDuel have no way to prove they picked the lineup before kickoff. Without a commitment hash, any track record is unfalsifiable.

We built **MT Fantasy Sports** -- 4 REST endpoints that create a two-step SHA-256 commitment wrapped in a W3C Verifiable Credential:

1. `lineup_hash = SHA-256(canonical JSON of lineup)`
2. `commitment_hash = SHA-256(agent_did:contest_id:lineup_hash:timestamp)`
3. Both hashes + metadata → signed `FantasyLineupCredential` (Ed25519)

The commitment is anchored on Base L2 before the contest. After the contest, settle with actual scores and prizes. The API aggregates ITM rate, ROI, and projection accuracy.

The VC wrapper matches every other MolTrust vertical (Shopping, Prediction, Skill, Salesguard). Same Ed25519 signing, same `did:web:api.moltrust.ch` issuer, same proof format. The SHA-256 commitment scheme does the heavy lifting -- the VC makes it portable and interoperable.

5 platforms (DraftKings, FanDuel, Yahoo, Sleeper, custom), 8 sports. Also available as 3 MCP tools (`pip install moltrust-mcp-server`, 36 tools total). Free during EA.

Developer guide: https://moltrust.ch/blog/fantasy-sports-developer-guide.html
API health: https://api.moltrust.ch/sports/health

---

## Show HN

**Title:** Show HN: W3C Verifiable Credentials proving your AI fantasy agent picked the lineup before kickoff

**Body:**

MT Fantasy Sports lets AI fantasy draft agents commit lineups with SHA-256 hashes anchored on Base L2 before contest start. Each commitment returns a signed FantasyLineupCredential (W3C VC, Ed25519).

The flow: agent submits a lineup to POST `/sports/fantasy/lineups/commit`. The API computes `SHA-256(canonical JSON of lineup)` for the lineup hash, then `SHA-256(agent_did:contest_id:lineup_hash:timestamp)` for the commitment hash. Both hashes, the platform, sport, and Base anchor go into a `FantasyLineupCredential` signed with Ed25519. The credential is returned in the response and stored on-chain. After the contest, the agent settles with actual score, rank, and prize via PATCH. The verify endpoint (public, no auth) shows the full lineup with `minutes_before_contest` and the signed credential.

The SHA-256 commitment scheme handles timing proof. The W3C VC wrapper makes it portable -- same standard, same issuer DID, same Ed25519 signing as every other MolTrust vertical (Shopping, Prediction, Skill, Salesguard).

Stats track across lineups: ITM rate, ROI, projection accuracy, platforms, and sports. An agent's fantasy track record is built from committed, settled data -- not self-reported numbers.

Stack: Python/FastAPI, SHA-256 canonical JSON hashing, Ed25519 VC signing, Base L2 anchoring.

4 endpoints. 5 platforms (DraftKings, FanDuel, Yahoo, Sleeper, custom). 8 sports. 3 MCP tools. Free during Early Access.

Developer guide: https://moltrust.ch/blog/fantasy-sports-developer-guide.html
API health: https://api.moltrust.ch/sports/health
