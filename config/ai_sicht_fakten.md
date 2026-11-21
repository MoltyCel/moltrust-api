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

## How to judge

- "richtig": every statement about MolTrust, Lars Kroehl or AAE matches the
  facts above or the scout fact sheet.
- "teilweise": the core is right, single details are missing or vague.
- "veraltet": a statement matched an earlier state (for example PDLSS instead
  of AAE, an older draft revision, Lightning payments, GitHub OAuth).
- "erfunden": a statement contradicts the facts or has no basis in them
  (invented founders, locations, funding, products, partnerships).
- "nicht_genannt": the answer does not identify MolTrust, Lars Kroehl or AAE,
  or says it does not know them.
