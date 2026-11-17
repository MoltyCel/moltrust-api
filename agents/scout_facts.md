# MolTrust fact sheet for the Scout briefing

`agents/scout.py` reads this file on every run and gives it to the model as
the only description of MolTrust. Every line carries the place on `main`
that backs it (`path:line`, moltrust-api unless marked `moltrust-web`).
Change a fact here only together with its source; a claim without a source
does not belong in this file.

Everything above the first `##` heading is for maintainers and is not sent
to the model.

## What MolTrust is

- Trust infrastructure for autonomous AI agents, built on W3C DIDs and W3C
  Verifiable Credentials. (README.md:3-4, .well-known/agent-card.json:3)
- Positioning line: "TCP/IP for Agent Trust". (docs/audits/vcone_audit_20260424.md:216)
- Core message: "Identity tells you who an agent is. Authorization tells you
  what it is allowed to do." (moltrust-web index.html:1184-1185)
- Authorization is checked before the transaction, not reconstructed from
  reputation afterwards ("pre-transaction trust").
  (moltrust-web blog/aae-agent-authorization-envelope.html:441)
- Standards-based and verifiable by anyone, not proprietary: DID documents
  carry the on-chain public key, so verification works offline via
  @moltrust/verify. (README.md:32-37)

## Identity

- Agent identity is the DID method `did:moltrust` (W3C DID). New agents get
  `did:moltrust:<16 hex>`. (app/main.py:1869, app/main.py:1261, agents/scout.py:8)
- Ed25519 public keys are anchored on Base L2 at registration.
  (README.md:11, README.md:32-35)

## Authorization

- Agent Authorization Envelope (AAE): MANDATE / CONSTRAINTS / VALIDITY,
  issued as W3C Verifiable Credentials. (README.md:19, .well-known/agent-card.json:37)

## Payments

- USDC on Base: incoming USDC transfers on Base are polled and booked.
  (monitor/poll_payments.py:3-4, agents/poll_payments.py:18, app/main.py:4455)
- x402: paid endpoints accept x402 payments; the x402 `payTo` is the
  MolTrust Base address. (CLAUDE.md:52-54, app/main.py:113)
- @moltrust/x402 is an offline trust gate for x402 endpoints.
  (packages/x402/package.json:2-4, app/main.py:4273)

## Numbers about MolTrust

- Agent counts come only from the canonical count behind `/stats`
  (docs/agent-counting.md:3-5). This sheet gives no agent count, so the
  briefing states none.

## Never state these as MolTrust properties

- Lightning payments. The route `/payment/lightning/invoice` is a stub that
  returns `"status": "pending"` and creates no invoice; it is not a payment
  rail. (app/main.py:3659-3662)
- GitHub OAuth as a product or trust feature. `/auth/github` is an old
  sign-in route that only creates a `did:moltrust` entry; it carries no trust
  signal. (app/main.py:5816-5870)
- `did:web` as MolTrust's identity method. Agents are `did:moltrust`;
  `did:web:api.moltrust.ch` only names the platform's own issuer key.
  (app/main.py:3702-3711)
- PDLSS (that is a competitor's term; MolTrust uses AAE).
  (moltrust-enterprise-business-case.md:101, README.md:19)
- "600 agents" or any other agent count not taken from `/stats`.
  (docs/agent-counting.md:3-5)
