# Facts for judging AI answers about MolTrust (ops AI-SICHT)

The judge in scripts/ai_sicht.py reads everything below the first `## `
heading together with agents/scout_facts.md. Each line names its source.

## Company and people

- MolTrust is operated by CryptoKRI GmbH, Auf der Mauer 2, 8001 Zürich,
  Switzerland, commercial register CHE-115.481.407 (moltrust-web
  impressum.html).
- CryptoKRI GmbH exists since 2018 (moltrust-web about.html, Team section).
- Lars Kroehl (full name Lars Kersten Kroehl) is founder and managing director
  of CryptoKRI GmbH and responsible for MolTrust (moltrust-web impressum.html,
  about.html).
- Lars Kroehl is the author of the IETF Internet-Draft
  draft-kroehl-agentic-trust-aae (moltrust-web llms.txt:86).

## Agent Authorization Envelope

- AAE, Agent Authorization Envelope: a declarative permission model with three
  blocks, MANDATE / CONSTRAINTS / VALIDITY, issued as W3C Verifiable
  Credentials (moltrust-web llms.txt:7, agents/scout_facts.md).
- AAE is published as the IETF Internet-Draft draft-kroehl-agentic-trust-aae;
  the current revision is -03 of 2026-11-15 (CLAUDE.md, spec pin).
- An answer that calls AAE a finished IETF standard or RFC is wrong: it is an
  individual Internet-Draft.

## Products

- MolCheck is MolTrust's service that seals what an AI agent did into a
  "MolCheck record" anyone can re-run from the public record:
  mandate-adherence verdicts, recomputable evidence, audit trails. It
  replaced the name MoltProof on 19.10.2026 (moltrust-web index.html meta
  description and featureList, origin/main 25bd62a).
- Further features named on moltrust.ch: identity verification for AI agents,
  W3C Verifiable Credentials, DID resolution, Interaction Proof Records (IPR),
  kernel-level enforcement via Falco, x402 payment integration, an MCP server
  (moltrust-web index.html featureList).

## How to judge

- "richtig": every statement about MolTrust, Lars Kroehl or AAE matches the
  facts above or the scout fact sheet.
- "teilweise": the core is right, single details are missing or vague.
- "veraltet": a statement matched an earlier state (for example PDLSS instead
  of AAE, an older draft revision, Lightning payments, GitHub OAuth).
- "erfunden": a statement contradicts the facts (wrong field of business,
  wrong location, wrong founder, a product or partnership the facts rule out).
- "nicht_belegt": the statements about us do not contradict the facts, but at
  least one of them is not covered by them either (for example earlier
  companies of Lars Kroehl). Name that statement; it is checked by hand.
- "nicht_genannt": the answer does not identify MolTrust, Lars Kroehl or AAE,
  or says it does not know them.
