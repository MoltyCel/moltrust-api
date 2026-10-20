# ADR-LC1 — Leak canary for delegating agents

**Status:** ACCEPTED (2026-10-20)
**Date:** 2026-10-20 · **Author:** Lars Kroehl
**Relates to:** [`ADR-D3-mandate-enforcement-v3.md`](ADR-D3-mandate-enforcement-v3.md) (enforce kernel, unchanged by this record), [`ADR-AER-attested-evidence-replay.md`](ADR-AER-attested-evidence-replay.md) (evidence format the leak event uses). Description for integrators: [`docs/leak-canary.md`](../leak-canary.md). Ops job LC-1.

## Context

A delegating agent hands credentials to counterparties. If one of them leaks a
credential, by negligence or compromise, the delegating agent learns about it
late or never, and when it does learn, it cannot say which counterparty the
leak came through.

Canary tokens solve the first half of that for secrets in general: a value
that nobody should ever use, and whose use raises an alarm. For credentials
handed to several counterparties the second half matters as much: one canary
per counterparty turns "something leaked" into "this counterparty leaked it".

## Decision

MolTrust issues canary credentials per issuer and counterparty, detects their
presentation at its verifier, and answers with a signed leak event and, if the
issuer asked for it, a hold on that counterparty.

### 1. A canary is an ordinary credential

Canaries are issued through the same function as `POST /credentials/issue`,
with the same claims, keys, validity, storage, IPFS publication and anchoring.
No field, type, identifier pattern, validity period or status entry marks a
canary. The mapping from a credential's proof to issuer and counterparty lives
in one server-side table, `leak_canaries`, and nowhere else.

The mechanism is public; the instances are not. A holder who knows how canaries
work still cannot tell whether the credential in front of it is one. A public
test holds the structural equality of a canary and a regular credential from
the same issuer. It compares structure only, never values.

The list of properties by which a canary could still be told apart — timing of
issuance, subject DID registration, presentation history and similar — is kept
in a private repository and reviewed there. It is not part of this record.

### 2. Two modes

`bound` issues the credential to the counterparty's own DID; the issuer and the
counterparty are expected presenters. `decoy` issues it to a fresh DID that
belongs to nobody; only the issuer is an expected presenter. Every other
presenter, including one that sends no API key, triggers an event.

### 3. Detection at the verifier, after the answer

`POST /credentials/verify` looks up the proof of every presented credential in
`leak_canaries`. The lookup runs for every presentation, canary or not, and the
event is written in a background task after the response. The presenter's
answer and its timing therefore do not depend on whether the credential is a
canary.

The token endpoint is not covered here; it is ops job LC-4.

### 4. The leak event is signed and carries AER material

The event is signed with the registry key under its own domain tag
(`moltrust:leak-canary-event:v1` + `0x00`), the same construction as the
verdict signatures in `app/enforcement/verdict_sign.py`.

It also carries a complete AER decision: one evidence item signed by
`did:web:api.moltrust.ch#leak-canary` stating that the canary was presented,
a one-grant mandate that allows `{"verb": "leak_canary_hold"}` only on that
evidence, the bundle and the `f_ext` record. A third party recomputes it with
`moltrust-verify` and a trust list, without contacting MolTrust. The event
signature says MolTrust wrote this event; the AER material says the hold
follows from signed evidence, and anyone can recompute that step.

### 5. Hold through the mandate, not beside the kernel

The enforce kernel stays pure and unchanged. A hold is expressed in the
issuer's mandate: `POST /canary/enforce-hook` inserts, before each `allow`
grant, a copy with disposition `hold` and one extra `exact` constraint on the
transaction field that names the counterparty. The kernel's first-match rule
then yields `PENDING` for held counterparties and leaves every other
transaction as it was. The patched mandate is a normal mandate; its verdicts
recompute like any other.

`forbid` per counterparty is not offered. The kernel applies `forbid` to the
action binding before constraints are evaluated, so a forbid copy would forbid
the action for every counterparty. Changing that would change the kernel and
its parity copy in the SDK, which this record does not do.

### 6. Privacy

No personal data: DIDs, digests and timestamps only, no IP address or user
agent. The presenter appears only as the DID behind its API key. Every canary
endpoint returns the calling issuer's rows only, and answers a foreign or
unknown identifier with the same `404`.

## Consequences

- Issuing a canary costs the same as issuing a credential, so canaries are no
  way to obtain credentials for less.
- Every verify call makes one indexed lookup more.
- A canary only sees presentations at the MolTrust verifier. Offline signature
  checks by a holder leave no trace.
- The tables are new and owned by the application role
  (`migrations/2026-10-20_leak_canary.sql`); the migration is applied by hand,
  as every migration in this repository.

## Not built

Integrity phase and a clean-record credential, Thinkst or Tracebit integration,
on-chain anchoring of events, a PyPI release of the SDK, and the token-endpoint
trigger (LC-4). The protocol TechSpec in the website repository is updated in
ops job LC-3.
