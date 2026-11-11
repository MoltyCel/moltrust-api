# MolTrust ERC-8004 Outreach Strategy
# Author: MolTrust / CryptoKRI GmbH
# Date: 2026-04-09
# Status: Draft for Review — NOT YET EXECUTED
# Goal: Mass adoption via direct outreach to 44,355 ERC-8004 registered agents

---

## Review Context (for reviewers)

MolTrust is a W3C DID/VC-based trust infrastructure for autonomous AI agents, live at api.moltrust.ch. Today we built `@moltrust/x402` (npm), a trust score middleware for x402 payment endpoints. We are now planning an outreach campaign to the entire ERC-8004 agent registry.

**What we want reviewers to assess:**
1. Is XMTP the right outreach channel for this audience?
2. Is the batch size and rate (500/day) appropriate, or are there spam/reputation risks?
3. Is the message content compelling enough to drive registrations?
4. What conversion rate is realistic?
5. What are the legal/ethical risks of mass unsolicited XMTP messaging?
6. Is there a better approach we are missing?
7. What would you change to maximize registrations while minimizing spam risk?

**Team:** 1 founder, 1 part-time engineer. Self-funded. No marketing budget.

---

## What We Found

### The ERC-8004 Registry

ERC-8004 is an on-chain standard for AI agent identity on Ethereum/Base. Agents register their wallet address, DID, and metadata on-chain. The registry is public and queryable.

**Scan results (2026-04-09, via Goldsky Subgraph):**

| Metric | Value |
|--------|-------|
| Total ERC-8004 Agents on Base | 44,355 |
| Already registered at MolTrust | 4 |
| Outreach candidates | 44,351 |
| Scan time | ~60 seconds |

This is the largest available pool of verified AI agents globally. Every agent in this registry has:
- A verified on-chain wallet address
- A declared agent identity
- Demonstrated intent to participate in the agent economy

### XMTP Feasibility

XMTP allows sending messages to any Ethereum wallet address that has enabled XMTP. Not all wallets are XMTP-enabled.

**Estimated XMTP-capable wallets:**
- Conservative: 2-5% = 887–2,218 reachable agents
- Optimistic: 10-15% = 4,435–6,653 reachable agents

Infrastructure built today:
- `scripts/outreach_xmtp.js` — Node.js XMTP client using MolTrust operator wallet
- `erc8004_outreach` DB table — 44,351 candidates loaded
- `outreach_sent` DB table — deduplication, never repeat
- Dry run completed successfully (exit 0)

### Current Outreach Pool (combined)

| Source | Candidates |
|--------|-----------|
| ERC-8004 Registry | 44,351 |
| payment_events (10+ x402 tx) | 1 |
| **Total** | **44,352** |

---

## Proposed Approach

### Message Content

```
Subject: Your ERC-8004 Agent #{agent_id} — MolTrust Trust Score

You are registered as ERC-8004 Agent #{agent_id}.

MolTrust adds W3C DID-based identity and verifiable credentials
to your ERC-8004 identity — free, takes 2 minutes.

Your trust profile: https://moltrust.ch/wallet/{wallet_address}

Benefits:
- Portable trust score across all x402 endpoints
- @moltrust/x402 middleware access (npm install @moltrust/x402)
- W3C Verifiable Credentials anchored on Base L2
- EU AI Act compliance documentation

Register free: https://moltrust.ch/register?erc8004={agent_id}

The MolTrust Team
https://moltrust.ch
```

### Sending Parameters

| Parameter | Proposed Value | Rationale |
|-----------|---------------|-----------|
| Batch size | 500/day | Balance between speed and spam risk |
| canMessage() check | Yes — skip non-XMTP wallets | No point sending to deaf wallets |
| Repeat sends | Never — 1 message per wallet | Respect for recipients |
| Sending wallet | MolTrust Operator (0x3802..., legacy since 2026-10-12; now 0xD63f...) | Consistent sender identity |
| Start time | After review approval | Not yet executed |
| Cron | Daily 07:00 UTC | Off-peak hours |

### Timeline

At 500/day and ~5% XMTP-capable:
- Day 1: canMessage() check on first 10,000 → ~500 XMTP-capable identified
- Day 1-9: All ~2,200 conservative XMTP-capable agents reached
- Day 1-30: All ~6,600 optimistic XMTP-capable agents reached

### Expected Conversions

| Scenario | XMTP-Capable | Open Rate | Conversion | New Agents |
|----------|-------------|-----------|------------|------------|
| Conservative | 2,218 | 20% | 5% | ~22 |
| Base case | 4,435 | 30% | 10% | ~133 |
| Optimistic | 6,653 | 40% | 15% | ~399 |

Current MolTrust agents: 39. Even the conservative scenario represents +56% growth.

---

## Risks and Mitigations

### 1. Spam Reputation Risk
**Risk:** Sending 44,000 unsolicited messages could flag the MolTrust operator wallet as a spammer, reducing future XMTP deliverability.
**Mitigation:** canMessage() check filters to willing recipients; 1 message per wallet rule; clear unsubscribe mechanism in message.

### 2. Legal Risk (GDPR / CAN-SPAM)
**Risk:** Mass unsolicited messaging may violate EU communication regulations.
**Analysis:** XMTP messages go to wallet addresses, not personal email addresses. Recipients are businesses/developers operating agents, not consumers. However this is untested legal territory.
**Mitigation:** Message is informational, not promotional; clear sender identity; no personal data processed.

### 3. Low XMTP Adoption
**Risk:** If only 1% of ERC-8004 wallets are XMTP-enabled, we reach only ~444 agents.
**Mitigation:** Acceptable — even 444 targeted outreaches to verified agent operators is valuable. Alternative channels (GitHub, HTTP contact endpoints) can supplement.

### 4. Message Ignored
**Risk:** Even XMTP-capable agents may ignore the message.
**Mitigation:** Personalized with agent ID; clear value proposition; shadow score page already built so recipients see immediate value when clicking the link.

### 5. Negative Reaction
**Risk:** Some recipients may publicly complain about unsolicited messages, damaging MolTrust reputation.
**Mitigation:** Message is respectful, opt-in forward (register if interested), one-time only.

---

## Alternative Approaches (for reviewer consideration)

**Option A — GitHub outreach only**
Many ERC-8004 agents have public GitHub repos. MoltyCel posts a comment or issue. Lower reach (maybe 500 repos discoverable) but higher trust signal.

**Option B — HTTP contact endpoint**
Some ERC-8004 agents expose `/.well-known/agent.json` with contact info. Automated HTTP POST with partnership proposal. Very low spam risk.

**Option C — Do nothing and wait**
Let agents find MolTrust organically via awesome-erc8004 listing, x402 middleware, and search. Slower but zero spam risk.

**Option D — Hybrid (recommended by author)**
XMTP to XMTP-capable wallets + GitHub outreach to repos with public code + HTTP to agents with contact endpoints. Maximizes reach while diversifying channels.

---

## What Is Already Built

| Component | Status |
|-----------|--------|
| ERC-8004 Scanner (Goldsky Subgraph) | ✅ Live, daily cron 06:30 UTC |
| erc8004_outreach DB table | ✅ 44,351 candidates loaded |
| outreach_xmtp.js | ✅ Dry run successful |
| outreach_sent deduplication | ✅ Never repeat |
| Wallet Shadow Score page | ✅ moltrust.ch/wallet/{address} |
| @moltrust/x402 npm package | ✅ v1.0.0 live |
| Register URL with erc8004 param | ⏳ Not yet built |

**Nothing has been sent yet.** This document is for review before execution.

---

## Open Questions for Reviewers

1. Is XMTP legally and ethically acceptable for this use case?
2. Is 500/day the right batch size or should we start slower (e.g. 50/day)?
3. Is the message content compelling? What would you change?
4. Should we personalize further (include shadow score in message)?
5. What conversion rate is realistic for cold XMTP outreach to agent operators?
6. Is Option D (hybrid) the right approach, or go all-in on XMTP?
7. Any other risks we have not considered?

---

## References

- ERC-8004 Registry (Goldsky Subgraph): https://api.goldsky.com
- MolTrust API: https://api.moltrust.ch
- @moltrust/x402: https://www.npmjs.com/package/@moltrust/x402
- Wallet Shadow Score: https://moltrust.ch/wallet/
- awesome-erc8004: https://github.com/sudeepb02/awesome-erc8004
- XMTP Protocol: https://xmtp.org
