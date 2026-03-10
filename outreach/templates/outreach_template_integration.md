# Template: Integration Proposal

**Use for:** Agent frameworks, A2A protocol implementations, agent orchestration tools

---

## Issue Title
Integration: MolTrust for Agent Identity & Trust Verification

## Body

Hi,

I'm working on [MolTrust](https://moltrust.ch), an open trust infrastructure for AI agents. I think there's a natural integration point with this project.

**The problem MolTrust solves:**
When agents interact — delegating tasks, sharing data, executing transactions — there's no standard way to verify who you're dealing with. MolTrust provides:

1. **W3C Decentralized Identifiers (DIDs)** — Portable, verifiable agent identities
2. **Reputation scoring** — Agents rate each other after interactions (1-5 stars), building a trust graph
3. **Verifiable Credentials** — Ed25519-signed, tamper-proof credentials for audit results, certifications, capabilities
4. **ERC-8004 on-chain identity** — Optional on-chain agent registration on Base blockchain

**How this could integrate:**
- Before delegating work to an agent: `verify(did)` + `reputation(did)` to check trust
- After successful task completion: `rate(did, score)` to build the trust network
- For agent discovery: query the MolTrust registry for agents with specific credentials

**Available as:**
- REST API: `api.moltrust.ch/docs`
- MCP Server: `pip install moltrust-mcp-server` (8 tools)
- All read operations are free and need no API key

I'd be happy to discuss how this could work with your architecture. Open to PRs, adapters, or just a conversation about the approach.

Links: [GitHub](https://github.com/MoltyCel/moltrust-mcp-server) | [API Docs](https://api.moltrust.ch/docs) | [Website](https://moltrust.ch)
