# MolTrust API

Reference implementation of the [MolTrust Protocol](https://moltrust.ch) —
W3C DID + Verifiable Credential trust infrastructure for AI agents.

## Stack
- Python + FastAPI
- PostgreSQL
- Base L2 (on-chain anchoring)
- AWS KMS (key management)

## Features

| Vertical | Description |
|---|---|
| Agent Identity | W3C DID registration, resolution, on-chain public key anchoring |
| Authorization | Agent Authorization Envelope (AAE) — MANDATE / CONSTRAINTS / VALIDITY |
| Trust Score | Phase 2 Swarm Intelligence — peer endorsements + cross-vertical propagation |
| Output Provenance | Interaction Proof Records (IPR) — SHA-256 + Merkle batch anchoring |
| Shopping | BuyerAgentCredential verification + issuance |
| Travel | TravelAgentCredential — 10-step verification pipeline |
| Skills | VerifiedSkillCredential — 8-point security audit |
| Prediction | PredictionTrackCredential — wallet-to-DID bridge |
| Salesguard | ProductProvenanceCredential + AuthorizedResellerCredential |
| Sports | Predictions, signals, fantasy — integrity layer |
| Music | Verified provenance for AI-generated music |

## Public Key Anchoring

Ed25519 public keys are anchored on Base L2 at registration:
```
Calldata: MolTrust/DID/v1/<identifier>/<pubKeyHex>
```
DID documents include `verificationMethod` with the on-chain public key,
enabling full offline verification via [@moltrust/verify](https://github.com/MoltyCel/moltrust-verify).

## Protocol
- [Protocol Whitepaper v0.6.1](https://moltrust.ch/MolTrust_Protocol_Whitepaper_v0.6.1.pdf)
- [Technical Specification v0.4](https://moltrust.ch/MolTrust_Protocol_TechSpec_v0.4.pdf)

## API
Live at: `https://api.moltrust.ch` · [API Docs](https://api.moltrust.ch/docs)

## Related Packages
- [@moltrust/verify v1.1.0](https://github.com/MoltyCel/moltrust-verify) — Full offline VC + IPR verification
- [@moltrust/sdk](https://www.npmjs.com/package/@moltrust/sdk) — Agent verification middleware
- [@moltrust/aae](https://www.npmjs.com/package/@moltrust/aae) — Authorization Envelope
- [moltrust-mcp-server](https://github.com/MoltyCel/moltrust-mcp-server) — MCP server (48 tools)

## License
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)

## Contact
security@moltrust.ch
