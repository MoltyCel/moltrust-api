# MolTrust x402 Integration Strategy
# Author: MolTrust / CryptoKRI GmbH
# Date: 2026-04-09
# Status: Draft for Review
# Goal: Mass adoption of MolTrust as the trust standard for the x402 agent payment ecosystem

---

## Review Context (for reviewers)

MolTrust is a W3C DID/VC-based trust infrastructure for autonomous AI agents. It is live at api.moltrust.ch with 39 registered agents, active MCP traffic (400+ requests/day), and a functioning trust score system. MolTrust is already listed as an endpoint in the x402 ecosystem repository.

The x402 protocol enables AI agents to make micropayments (USDC on Base L2) to access APIs without accounts or API keys. Since launch in May 2025, x402 has processed 100M+ payments across 10,000+ endpoints. Foundation members include Coinbase, Cloudflare, Google, Visa, AWS, Circle, Anthropic, and Vercel.

**The core problem we are solving:** x402 enables agent payments but has no trust layer. Any agent can pay — there is no way for an endpoint operator to know if the paying agent is legitimate, has a behavioral history, or has been flagged for abuse. MolTrust fills this gap.

**The strategic goal:** Position MolTrust as the default trust layer for x402 — so that every x402 transaction optionally carries a MolTrust trust signal.

**Team:** 1 founder (Lars), 1 part-time infrastructure engineer (Harald). Self-funded.

**What we want reviewers to assess:**
1. Is the strategy coherent and sequenced correctly?
2. Are the adoption numbers realistic?
3. What are the biggest risks or blind spots?
4. What would you add, remove, or change to accelerate mass adoption?
5. Is there a faster path we are missing?

---

## The Strategy: Four Steps to x402 Trust Layer

### Step 1 — Build: @moltrust/x402 Middleware Package

**What:** An npm package that any x402 endpoint operator can install with one line of code:

```javascript
app.use(moltrust.requireScore({ minScore: 60 }));
```

This middleware intercepts every incoming x402 request, checks the paying agent's MolTrust trust score, and allows or denies the request based on a configurable threshold. Agents below the threshold receive a 403 response with a link to register at MolTrust.

**How it works:**
1. x402 request arrives at endpoint (agent pays USDC)
2. Middleware extracts the paying wallet address from the x402 payment header
3. Middleware calls MolTrust API: GET /wallet/{address} → returns trust score
4. If score ≥ minScore: request passes through
5. If score < minScore or wallet not registered: 403 + registration link
6. Every check is logged as an Interaction Proof Record (IPR) on Base L2

**What we build:**
- `@moltrust/x402` npm package (extends existing `@moltrust/sdk`)
- `/wallet/{address}` endpoint already live (built today)
- Shadow Score for unregistered wallets already live (built today)

**Timeline:** 1-2 sessions

**Why this first:** Without a product to offer, outreach is meaningless. This is the foundation everything else depends on.

---

### Step 2 — Seed: Bazaar Indexer + XMTP Outreach

**What:** The x402 Bazaar is a public discovery endpoint listing all x402-compatible services. Each service has a wallet address and a URL. We build an automated indexer that:

1. Fetches the Bazaar daily (all listed services)
2. Checks which wallet addresses are NOT yet registered at MolTrust
3. Sends an XMTP message to each unregistered wallet (one-time, never repeat)
4. Message: "Your service is listed in the x402 Bazaar. MolTrust can add trust verification to your endpoint — one npm package, one line of code. See: moltrust.ch/wallet/{address}"

**XMTP infrastructure:** Already built today. Script: `scripts/outreach_xmtp.js`. Sender wallet: MolTrust Operator Wallet (0x3802..., legacy since 2026-10-12; now 0xD63f...).

**Additional contact channels:**
- GitHub: MoltyCel posts to public repos of Bazaar-listed services (where repo is discoverable)
- HTTP: POST to `/.well-known/agent.json` contact endpoint where available

**Expected reach:** x402 Bazaar currently lists 10,000+ endpoints. XMTP messaging requires the recipient wallet to be XMTP-enabled. Estimated 10-30% of active wallets are XMTP-capable = 1,000-3,000 reachable services.

**Timeline:** 1 session (infrastructure mostly done)

---

### Step 3 — Credibility: x402 PR #1528

**What:** Submit a formal proposal to the x402 specification repository (coinbase/x402 PR #1528, already open) proposing a Trust Extension to the x402 protocol.

**Content of the proposal:**
- Optional `X-Trust-DID` header in x402 requests (agent identifies itself)
- Optional `X-Trust-Score` header in x402 responses (endpoint reports required score)
- Reference to MolTrust as the first implementation of this extension
- Link to working `@moltrust/x402` npm package as proof of concept

**Why this matters:** If merged or seriously discussed, MolTrust becomes part of the official x402 specification conversation. Foundation members (Coinbase, Cloudflare, Google, Anthropic) are watching this repo. This is the highest-leverage public visibility move available.

**Timeline:** 1 session (draft PR text + reference implementation)

---

### Step 4 — Scale: Bankr x402 Cloud Outreach

**What:** Bankr x402 Cloud (live since April 2, 2026) lets developers deploy a paid API endpoint in one command. Every endpoint deployed there is automatically indexed in the x402 discovery layer and generates on-chain transaction history.

**Outreach approach:**
- Direct contact to Bankr team: propose native MolTrust integration as optional trust layer for all Bankr-deployed endpoints
- If accepted: every new Bankr endpoint gets a MolTrust trust check by default
- This is a B2B platform deal, not individual developer outreach

**Why Bankr specifically:** They are the largest aggregator of new x402 endpoints. A single integration deal gives us access to their entire endpoint catalog without individual outreach to each developer.

**Timeline:** 1 email / async conversation

---

## What We Expect in Numbers

### Conservative Scenario

| Milestone | Timeline | Registered Agents |
|-----------|----------|-------------------|
| Today | Apr 2026 | 39 |
| @moltrust/x402 live | May 2026 | 39 |
| Bazaar XMTP outreach sent | May 2026 | 39 |
| First endpoint operators register | Jun 2026 | 100-200 |
| PR #1528 merged/discussed | Jun-Jul 2026 | 200-500 |
| Bankr integration (if accepted) | Jul-Aug 2026 | 500-2,000 |
| End of 2026 | Dec 2026 | 1,000-3,000 |

### Optimistic Scenario (Bankr deal + PR merged)

| Milestone | Timeline | Registered Agents |
|-----------|----------|-------------------|
| Bankr integration live | Jul 2026 | 5,000-10,000 |
| End of 2026 | Dec 2026 | 10,000-50,000 |

### Revenue Projection (conservative)

| Agents | Monthly Protocol Fees | Platform Revenue |
|--------|----------------------|-----------------|
| 1,000 | ~$100 | ~$0 (no platform deals yet) |
| 10,000 | ~$1,000 | ~$5,000-10,000 (1-2 platform deals) |
| 50,000 | ~$5,000 | ~$50,000+ |

---

## Key Risks

1. **x402 ecosystem stays fragmented** — 10,000 endpoints but no dominant platform. Outreach to individual developers is high effort, low conversion.

2. **XMTP adoption is low** — if only 5% of x402 wallets are XMTP-enabled, our outreach reach drops to ~500 services.

3. **Bankr says no** — if the largest aggregator declines, we fall back to individual developer outreach.

4. **x402 itself doesn't reach mass adoption** — we are betting on x402 as the dominant agent payment protocol. If another protocol wins (ERC-4337 paymasters, Stripe agent billing, etc.), our positioning needs to shift.

5. **Someone builds the same thing faster** — AstraSync, Nevermined, Skyfire are all working in adjacent spaces. First-mover advantage is real but not permanent.

---

## Open Questions for Reviewers

1. Is the four-step sequence correct, or should we reorder?
2. Is XMTP the right outreach channel, or is there a higher-conversion alternative?
3. Should we target endpoint operators (supply side) or agent builders (demand side) first?
4. Is the Bankr partnership the right B2B target, or are there better aggregators?
5. What would make an endpoint operator actually install our middleware? What is the compelling reason?
6. Is the revenue projection realistic given current x402 ecosystem size?
7. What is the fastest path to 1,000 registered agents?

---

## References

- x402 Protocol: https://x402.org
- x402 Ecosystem Repo: https://github.com/coinbase/x402
- Bankr x402 Cloud: https://bankr.bot
- MolTrust API: https://api.moltrust.ch
- MolTrust Protocol Whitepaper v0.7: https://moltrust.ch/MolTrust_Protocol_Whitepaper_v0.7.pdf
- @moltrust/sdk: https://www.npmjs.com/package/@moltrust/sdk
- Wallet Shadow Score (live): https://moltrust.ch/wallet/0x3802... (legacy wallet since 2026-10-12)
