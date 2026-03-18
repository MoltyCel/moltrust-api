# MT Salesguard — Reddit & Hacker News Draft

## Reddit Post (r/AI_Agents, r/MachineLearning, r/cryptocurrency)

**Title:** MT Salesguard: Product Provenance API for the A2A Economy — W3C VCs, Ed25519, Base L2

**Body:**

Counterfeit goods cost $500B/year. In an agent economy where shopping bots compare products autonomously, visual anti-counterfeiting (holograms, QR codes) is useless. The buyer is an algorithm.

We built MT Salesguard — a product provenance API that issues W3C Verifiable Credentials for brand-registered products.

**How it works:**

1. Brand registers → gets a DID (`did:web:`) + API key (`sg_xxx`)
2. Brand registers products → each gets a signed ProductProvenanceCredential (Ed25519 JWS)
3. Brand authorizes resellers → each gets an AuthorizedResellerCredential
4. Shopping agent verifies → one GET call returns `verified: true/false` + `risk_level`

**The stack:**

- W3C Decentralized Identifiers (DIDs) for brand identity
- W3C Verifiable Credentials (VCs) for provenance and reseller auth
- Ed25519 JWS compact serialization for signatures
- SHA-256 credential hashes anchored on Base L2
- x402 payment protocol for on-chain settlement

**5 endpoints, all free during Early Access:**

- `POST /salesguard/brand/register` — no auth
- `POST /salesguard/product/register` — Bearer token
- `POST /salesguard/reseller/authorize` — Bearer token
- `GET /salesguard/verify/:product_id` — public
- `GET /salesguard/reseller/verify/:reseller_did` — public

Also available as 3 MCP tools via `pip install moltrust-mcp-server` (33 tools total).

Developer guide: https://moltrust.ch/blog/salesguard-developer-guide.html
API: https://api.moltrust.ch/guard/salesguard/verify/:product_id
Landing page: https://moltrust.ch/salesguard.html

Built by MolTrust (CryptoKRI GmbH, Zurich). Open standards, no vendor lock-in.

---

## Hacker News Post

**Title:** Show HN: MT Salesguard – Product provenance API with W3C VCs for the A2A economy

**URL:** https://moltrust.ch/blog/salesguard-developer-guide.html

**Comment (if needed):**

We built this because shopping agents can't read holograms. In agent-to-agent commerce, the buyer is an algorithm — it needs machine-readable proof that a product is genuine.

MT Salesguard issues W3C Verifiable Credentials (Ed25519 JWS) for brand-registered products. One GET call returns verified/unverified + risk level. Credential hashes are anchored on Base L2.

5 REST endpoints, all free during Early Access. Also available as MCP tools (pip install moltrust-mcp-server).

Stack: W3C DIDs, W3C VCs, Ed25519, x402 on Base, Hono/TypeScript backend, PostgreSQL.

Happy to answer questions about the credential format, the signing pipeline, or how this fits into the broader A2A trust stack.
