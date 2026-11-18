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
  `did:moltrust:<16 hex>`. (app/main.py:1869, app/main.py:1261, agents/scout.py:17)
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

## ERC-8004 is an interop partner

- MolTrust is an evidence and validator layer on top of ERC-8004, not a
  competing registry: an existing ERC-8004 ID is linked, never re-minted.
  Decision of 18.09.2026. (docs/spec-fakten/erc-8004.md:3-13)
- ERC-8004 therefore never appears under THREATS, and its registry figures
  are not described as competition. It belongs in TOP TRENDS or as an
  interoperability opportunity.

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

## External figures

- Every figure in the briefing needs a primary source (the organisation that
  measured or announced it, or its own report) and the date of that source.
  A blog, newsletter, aggregator or tracker page that repeats a figure is not
  a primary source. A figure without a primary source and a date is left out.
  (ops SCOUT-FIX-W, 18.11.2026)
- x402 scale: the only accepted figure is Chainalysis, more than 138 million
  x402 transactions on Base, published 14.10.2026 (2026 North America
  crypto-adoption report). Multi-chain tracker totals (e.g. "200,680,559
  across 12 chains") are not used. A newer x402 figure needs a newer primary
  source and is added to this sheet first. (ops SCOUT-FIX-W, 18.11.2026)
- No XRP Ledger figures of any kind, with or without a source.
  (ops SCOUT-FIX-W, 18.11.2026; scout_20261117_1800 and scout_20261118_1800)
- Membership of a coalition, consortium, alliance or standards body is stated
  only with the primary source: the announcement of the body or of the member
  itself. Otherwise name the body without its members.
  (ops SCOUT-FIX-W, 18.11.2026)
