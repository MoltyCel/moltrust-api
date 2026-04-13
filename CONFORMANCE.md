# MolTrust Protocol — AIP Conformance Report

**Version:** 1.0  
**Date:** April 2026  
**Status:** Published  
**Reference Paper:** *AIP: Agent Identity Protocol for Verifiable Delegation Across MCP and A2A* — arXiv:2603.24775 [cs.CR]  
**TechSpec:** v0.8 — on-chain anchor Base L2 Block 44638521 / TX 0x0b36c771...

---

## Summary

The AIP paper (arXiv:2603.24775) introduces Invocation-Bound Capability Tokens (IBCTs) and identifies five features that a complete agent authorization protocol must jointly implement. The authors write:

> "We did not identify a prior implemented protocol that jointly combines public-key verifiable delegation, holder-side attenuation, expressive chained policy, transport bindings across MCP/A2A/HTTP, and provenance-oriented completion records."

This report documents that **MolTrust implements all five features** as a live, production protocol — not a research prototype.

---

## Feature Conformance Matrix

| IBCT Feature | MolTrust Implementation | TechSpec Reference |
|---|---|---|
| **F1 — Public-key verifiable delegation** | AAE `validity.holderBinding` + Ed25519Signature2020 over RFC 8785 canonical JSON. Each delegation step independently verifiable without a trusted intermediary. | §2.8, §3.2 |
| **F2 — Holder-side attenuation** | AAE `mandate.deniedActions` + `delegation.attenuationOnly: true`. Sub-agent AAEs enforced as strict subsets of parent AAEs. Validated by TV-005. | §2.8.1, §2.8.4 Rule 6 |
| **F3 — Expressive chained policy** | AAE `mandate` (action URI patterns, resource ABAC, depth cap 8 hops) + `constraints` (spend limits, jurisdiction, time windows, counterparty score gate). | §2.8, §3.3 |
| **F4 — Transport bindings MCP/A2A/HTTP** | `@moltrust/sdk` v1.1.0 middleware for HTTP. `@moltrust/mpp` v1.0.3 for MPP/x402. MCP server with 48 tools. A2A governance thread active (a2aproject/A2A#1628). | §8.4 |
| **F5 — Provenance-oriented completion records** | Interaction Proof Records (IPR): dual Ed25519 sequential signatures, SHA-256 outcome hash, UUID deduplication, Merkle batch anchoring on Base L2. | §2.4, §6 |

**Result: 5/5 IBCT features implemented.**

---

## Conformance Test Vectors

Five test vectors (TV-001 through TV-005) were run against the live MolTrust endpoint (April 2026):

| Vector | Scope | Result |
|---|---|---|
| TV-001 | AAE delegation narrowing — top-level agent | ✅ Pass |
| TV-002 | AAE delegation narrowing — sub-agent depth 2 | ✅ Pass |
| TV-003 | AAE delegation narrowing — sub-agent depth 3 | ✅ Pass |
| TV-004 | Deny-precedence: action matched by both allowedActions and deniedActions | ✅ Pass |
| TV-005 | Attenuation enforcement: sub-agent scope exceeds parent | ✅ Correctly rejected |

Shared canonicalization: JCS RFC 8785. Shared signing: Ed25519.

---

## Beyond IBCT Scope

| Capability | Description | Status |
|---|---|---|
| Behavioral Trust Score | Continuous 0–100 score from endorsement graph, interaction history, cross-vertical coverage, sybil detection. Registry-signed, publicly verifiable. | Live |
| W3C DID + VC Alignment | W3C DID Core v1.0 + VC Data Model 2.0. Any W3C-conformant verifier validates credentials without proprietary tooling. | Live |
| On-Chain Anchoring | Protocol artifacts anchored on Base L2. Verifiable via any block explorer. | Live |
| Offline Verification | `@moltrust/verify` v1.1.0 — full credential and AAE verification without API calls. | Live |
| Trust Tier 0 (KYC) | Developer identity credential via accredited KYC provider. No personal data on-chain. | Live |
| Sequential Action Safety (SAS) | Pre-execution detection of irreversible action sequences. Phase 1 WARN mode. | Live |
| MoltGraph | Relationship-specific trust signal: 2-hop neighbourhood, 45-day half-life decay. | Live |
| Kernel-Level Enforcement (Falco) | Falco eBPF enforcement of AAE `deniedActions` at syscall level. Reference implementation: github.com/HaraldeRoessler/moltrust-falco-bridge | Roadmap Q2 2026 |

---

## Technical Evidence

| Artifact | Reference |
|---|---|
| TechSpec v0.8 on-chain anchor | Base L2 Block 44638521 / TX 0x0b36c7718632fa71bff67e22fdd3615408243b3c178819a9f1e340d526378d65 |
| KYA v3.1 on-chain anchor | Base L2 Block 44098421 / TX 0x56d81e14... |
| Reference implementation | https://api.moltrust.ch |
| @moltrust/sdk v1.1.0 | https://www.npmjs.com/package/@moltrust/sdk |
| @moltrust/verify v1.1.0 | https://www.npmjs.com/package/@moltrust/verify |
| @moltrust/mpp v1.0.3 | https://www.npmjs.com/package/@moltrust/mpp |
| AIP reference paper | https://arxiv.org/abs/2603.24775 |

---

## Conclusion

The AIP paper identifies a protocol design space and concludes no prior implementation jointly covers it. MolTrust does — in production, with real partners, anchored on-chain, verifiable by any party without proprietary tooling.

IBCTs formalize the constraint model with precision. MolTrust adds the operational layer — trust scoring, behavioral continuity, sybil resistance — that a production agent economy requires. The two are complementary.

---

*MolTrust / CryptoKRI GmbH, Zurich · info@moltrust.ch · https://moltrust.ch*  
*Protocol: open (Apache 2.0 / CC BY 4.0).*

---

## MolTrust vs. AIP — Full Comparison

| Feature | AIP / IBCT | MolTrust |
|---|---|---|
| **Agent identity** | Public-key DID, Ed25519 | W3C DID Core v1.0, `did:moltrust` method, key rotation with epoch history |
| **Delegation** | Invocation-bound capability tokens, append-only chain | AAE `validity.holderBinding`, 8-hop chain, each link independently verifiable |
| **Attenuation** | Biscuit/Datalog — expressive, formally verifiable | AAE `deniedActions` + `attenuationOnly: true` — URI-pattern based, deterministic |
| **Policy expressiveness** | Datalog rules — arbitrary logical constraints | AAE `mandate` + `constraints`: spend limits, jurisdiction, time windows, counterparty score gate, resource ABAC |
| **Transport bindings** | MCP, A2A, HTTP | MCP (48 tools), A2A, HTTP (`@moltrust/sdk`), x402, MPP (`@moltrust/mpp`) |
| **Provenance records** | IBCT append-only token chain | Interaction Proof Records: dual Ed25519 sequential signatures, SHA-256 outcome hash, Merkle batch anchoring on Base L2 |
| **Trust scoring** | ✗ not in scope | 0–100 score: endorsement graph, interaction history, cross-vertical coverage, sybil detection. Registry-signed, publicly verifiable. |
| **Behavioral continuity** | ✗ not in scope | Principal DID continuity: violation records follow the principal across agent re-registrations |
| **Sybil resistance** | ✗ not in scope | Layered: dual-sig proofs, x402 economic cost, on-chain violation records, Jaccard cluster detection |
| **On-chain anchoring** | ✗ not in scope | Base L2: DID registrations, ViolationRecords, TechSpec versions — verifiable via any block explorer |
| **Offline verification** | Reference implementations in Python/Rust | `@moltrust/verify` v1.1.0 — full credential and AAE verification without API calls |
| **W3C alignment** | Custom token format | W3C DID Core v1.0 + VC Data Model 2.0. Any W3C-conformant verifier validates without proprietary tooling. |
| **Kernel enforcement** | ✗ not in scope | Falco eBPF — AAE `deniedActions` at syscall level (Roadmap Q2 2026) |
| **Sequential action safety** | ✗ not in scope | SAS: pre-execution detection of irreversible action sequences, Phase 1 live |

**Where AIP is stronger:** Biscuit/Datalog supports arbitrary logical constraints — temporal rules, compound conditions, recursive policies. MolTrust's AAE uses URI-pattern matching, which is simpler to implement and audit but less expressive for complex multi-condition policies. Formal Datalog-style constraints are on our roadmap.
