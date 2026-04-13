# MolTrust AAE Conformance Report

## IBCT Feature Matrix (arXiv:2603.24775)

Reference: *Intention-Bounded Capability Tokens for Autonomous Agent Authorization* (arXiv:2603.24775 [cs.CR])

The IBCT paper defines five features that a complete autonomous agent authorization protocol must jointly implement:

| # | IBCT Feature | MolTrust Component | Status |
|---|---|---|---|
| 1 | Public-key verifiable delegation | AAE `validity.holderBinding` + Ed25519 JWS (RFC 8785 canonical JSON) | Implemented |
| 2 | Holder-side attenuation | AAE `delegation.attenuationOnly: true` + `constraints.deniedActions` | Implemented |
| 3 | Expressive chained policy | AAE `mandate` + `constraints` (spend ceiling, jurisdiction, time window, counterparty score gate) | Implemented |
| 4 | Transport bindings (MCP / A2A / HTTP) | `@moltrust/sdk` middleware + `@moltrust/mpp` + 42-tool MCP server | Implemented |
| 5 | Provenance-oriented completion records | Interaction Proof Records: dual Ed25519 sequential signatures, Merkle batch anchoring on Base L2 | Implemented |

**Result: 5/5 features implemented in production.**

---

## IBCT Conformance Test Vectors

**Date:** 2026-04-13
**Endpoint:** `https://api.moltrust.ch/guard/governance/validate-capabilities`
**Evaluator:** MoltGuard Governance Attestation Service
**Test suite:** [`ibct-conformance/run_tests.py`](https://github.com/MoltyCel/moltrust-api/tree/main/ibct-conformance)

| Vector | Description | Expected | Result | Status |
|--------|-------------|----------|--------|--------|
| TV-001 | Valid single-hop delegation | permit / conditional | permit (score: 75) | PASS |
| TV-002 | Scope widening attempt (admin:* injection) | deny | deny (score: 75) | PASS |
| TV-003 | Budget ceiling violation ($999,999) | deny / conditional | conditional (score: 75) | PASS |
| TV-004 | Expired credential (timestamp: 2020-01-01) | deny | deny (score: 75) | PASS |
| TV-005 | Unknown agent (zero-padded DID) | deny | deny (score: 0) | PASS |

### Vector Details

| Vector | Payload Summary | Trust Score | JWS Present | Spend Limit |
|--------|----------------|-------------|-------------|-------------|
| TV-001 | DID: `did:moltrust:vcone`, scope: `[data:read]`, amount: $100 | 75 | Yes | 10,000 |
| TV-002 | DID: `did:moltrust:vcone`, scope: `[data:read, data:write, admin:*]` | 75 | Yes | 0 |
| TV-003 | DID: `did:moltrust:vcone`, scope: `[commerce:checkout]`, amount: $999,999 | 75 | Yes | 10,000 (capped) |
| TV-004 | DID: `did:moltrust:vcone`, scope: `[data:read]`, eval timestamp: 2020-01-01 | 75 | Yes | 0 |
| TV-005 | DID: `did:moltrust:0000000000000000`, scope: `[data:read]` | 0 | Yes | 0 |

**Result: 5/5 test vectors passed.**

---

## On-Chain Anchors (Base L2)

All protocol documents are anchored as self-send transactions with `MolTrust/DocumentIntegrity/1 SHA256:<hash>` calldata from the MoltGuard operator wallet.

**Wallet:** `0x380238347e58435f40B4da1F1A045A271D5838F5`

| Document | SHA256 | Block | TX |
|----------|--------|-------|-----|
| KYA Whitepaper v3.1 | `871ea42bb8cb1765d5b0a8b6983c94ac05cadeb149610ff21f8e041f167fc047` | 44098421 | [`0x56d81e14...fb2c38`](https://basescan.org/tx/0x56d81e14daa94a00ad12db60d18d132a7831ad7345e4e864dcb6f75b42fb2c38) |
| Protocol TechSpec v0.8 | `cbf10c2e8e4e213b1ed773fe397a9c755f8981d869cba4decfd51aa2c18f1bc4` | 44638521 | [`0x0b36c771...378d65`](https://basescan.org/tx/0x0b36c7718632fa71bff67e22fdd3615408243b3c178819a9f1e340d526378d65) |

### Verification

Any party can verify document integrity without proprietary tooling:

```bash
# 1. Download PDF
curl -O https://moltrust.ch/MolTrust_Protocol_TechSpec_v0.8.pdf

# 2. Compute hash
sha256sum MolTrust_Protocol_TechSpec_v0.8.pdf
# Expected: cbf10c2e8e4e213b1ed773fe397a9c755f8981d869cba4decfd51aa2c18f1bc4

# 3. Compare against on-chain calldata at Block 44638521
# TX input (UTF-8): MolTrust/DocumentIntegrity/1 SHA256:cbf10c2e...1bc4
```

---

## Conclusion

MolTrust AAE evaluator passes **5/5** IBCT conformance vectors and implements **5/5** IBCT features defined in arXiv:2603.24775. All protocol documents are anchored on Base L2 with verifiable SHA256 hashes.

Reference implementation: [api.moltrust.ch](https://api.moltrust.ch)
Protocol: open (Apache 2.0 / CC BY 4.0)

---
*Generated 2026-04-13 — MolTrust / CryptoKRI GmbH, Zurich*
