# RFC: Native Agent Identity & Trust Verification for OpenClaw

**Author:** MolTrust (CryptoKRI GmbH)
**Date:** 2026-03-18
**Status:** Proposal
**Related:** [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004), [W3C DID](https://www.w3.org/TR/did-core/), [W3C VC](https://www.w3.org/TR/vc-data-model-2.0/)

## Summary

OpenClaw has no native agent identity system. Agents can hold wallets, execute payments, install skills autonomously, and communicate across platforms — but they cannot cryptographically prove who they are.

This RFC proposes adding a **trust verification hook** to OpenClaw core, enabling plugins to verify agent identity before skill installation, payment execution, or inter-agent communication.

## Problem

- **341 malicious skills** found on ClawHub (Koi Security, January 2026)
- **13.4%** of scanned ClawHub skills had critical security issues (Snyk)
- **135,000** OpenClaw instances exposed with default configuration

VirusTotal scanning (currently integrated) catches known malware signatures but cannot detect:
- Prompt injection attacks
- Agent impersonation
- Slow-burn trust accumulation before payload activation
- Sybil clusters (multiple fake identities controlled by one actor)

## Proposed Solution

### 1. Trust Verification Hook

Add an optional `onAgentVerify` hook to the OpenClaw plugin lifecycle:

```typescript
interface TrustVerificationResult {
  verified: boolean;
  score: number;        // 0–100
  grade: 'A' | 'B' | 'C' | 'D' | 'F';
  did?: string;         // W3C DID if available
  credentials?: VerifiableCredential[];
  warnings?: string[];
}

interface OpenClawPlugin {
  // Existing hooks...
  onAgentVerify?(agentId: string): Promise<TrustVerificationResult>;
}
```

### 2. Verification Points

Trust verification should be triggerable at:
- **Skill installation** — before installing a skill from ClawHub
- **Payment execution** — before sending funds to another agent
- **Inter-agent communication** — before accepting tasks from unknown agents
- **Gateway startup** — self-verification of the gateway's own identity

### 3. Trust Score Standard

Standardize a 0–100 trust score with letter grades:

| Score | Grade | Meaning |
|-------|-------|---------|
| 80–100 | A | Verified identity, clean history |
| 60–79 | B | Generally trustworthy, minor gaps |
| 40–59 | C | Limited history, proceed with caution |
| 0–39 | D/F | High risk, sybil signals detected |

### 4. Identity Standards

Support existing W3C standards rather than creating new ones:
- **W3C Decentralized Identifiers (DIDs)** for agent identity
- **W3C Verifiable Credentials** for trust attestations
- **ERC-8004** for on-chain agent registration

## Reference Implementation

We've built `@moltrust/openclaw` as a reference implementation:

```bash
openclaw plugins install @moltrust/openclaw
```

Features:
- `moltrust_verify` — agent tool to verify any W3C DID
- `moltrust_trust_score` — 0–100 reputation score by DID or wallet
- `/trust` and `/trustscore` slash commands
- Self-verification on gateway startup
- Gateway RPC methods for automation

Source: [github.com/MoltyCel/moltrust-openclaw](https://github.com/MoltyCel/moltrust-openclaw)
npm: [@moltrust/openclaw](https://www.npmjs.com/package/@moltrust/openclaw)

## Design Principles

1. **Opt-in** — Trust verification should be optional, not mandatory
2. **Pluggable** — Multiple trust providers should be supported via the hook interface
3. **Standards-based** — Use W3C DID/VC, not proprietary identity systems
4. **Free tier** — Basic verification should not require payment
5. **Decentralized** — No single authority should control agent identity

## Security Considerations

- Trust scores should be treated as advisory, not authoritative
- Multiple trust providers reduce single-point-of-failure risk
- On-chain anchoring provides tamper-evident audit trails
- Sybil detection requires cross-provider signal aggregation

## Open Questions

1. Should `onAgentVerify` be blocking or non-blocking by default?
2. Should OpenClaw core ship with a default trust provider, or remain provider-agnostic?
3. How should trust scores propagate in multi-hop agent delegation chains?
4. Should skill publishers be required to have a verified DID for ClawHub listing?

## References

- [MolTrust KYA Whitepaper](https://moltrust.ch/MolTrust_KYA_Whitepaper.pdf)
- [ERC-8004: Decentralized AI Agent Identity](https://eips.ethereum.org/EIPS/eip-8004)
- [W3C DID Core](https://www.w3.org/TR/did-core/)
- [W3C Verifiable Credentials](https://www.w3.org/TR/vc-data-model-2.0/)
- [@moltrust/openclaw on npm](https://www.npmjs.com/package/@moltrust/openclaw)
