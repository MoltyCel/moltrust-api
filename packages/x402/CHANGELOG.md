# Changelog

## 2.3.0 — unreleased

### Added

- `@moltrust/x402/x402-gate`: a receiver-side gate for x402 payments. Register
  `requireMoltrustVerdict()` with `onBeforeVerify` and `onBeforeSettle` on an
  `x402ResourceServer` or `x402Facilitator` from `@x402/core`. A payment passes
  only with a MolTrust verdict bound to this one payment, recomputed locally;
  every other path aborts. No network call in the check. Same rules and same
  vectors as `moltrust_enforce.x402_gate` in the Python SDK (moltrust-enforce
  0.9.0); every vector reaches the same decision with a byte-identical
  `core_digest`.
- `checkPayment`, `x402Transaction`, `trustMandates`, `mandateDigest`, and for
  the agent side `buildReceipt` and `buildX402Headers`.
- `enforce-core.js`: the enforce-mode verdict engine (enforce_version 3.0),
  ported from the Python SDK and replayed against the AAE conformance vectors.
- Runnable examples for Express and Hono under `examples/` (not part of the
  published package).

### Unchanged

- `requireMolTrust` and everything else in `index.js`.

## 2.2.1 — 2026-11-16

### Changed

- Published from a GitHub Actions workflow through npm Trusted Publishing,
  with a provenance statement linking the tarball to its commit and run.
  `package.json` now names its source repository (`MoltyCel/moltrust-api`,
  directory `packages/x402`). No code change.

## 2.2.0 — 2026-10-31

### Added

- A gate built without `operatorDid` registers a MolTrust DID for its operator
  once, in the background, after its first decision: Ed25519 key pair generated
  locally, public key registered through `/identity/register-challenge` and
  `/identity/register-pop` with the platform `usage-x402`, DID and key stored in
  `~/.moltrust/x402-identity.json` (mode 0600). README "Automatic operator DID".
- `autoRegister` option; `false` turns the registration off, as does
  `MOLTRUST_NO_AUTO_REGISTER=1`. Not run when `CI` is set.

### Unchanged

- Every decision and every 403. The request path makes no network call, and the
  invite still appears only with an `operatorDid` the integrator set.

## 2.1.0 — 2026-10-16

### Added

- `operatorDid` option. Set it to your own `did:moltrust:` identifier and a
  caller denied with `attestation_missing` is invited to register free of
  charge, with you as the referrer. An agent that registers that way is
  recorded as having come from you, and MolTrust issues you a
  `ReferralCredential` for it.
- The 403 carries a `moltrustGate` block — `reason`, `detail`, and `invite`
  where one applies. It is the same block the moltguard service puts in its
  402, so a caller sees one shape whichever of the two turned it away.
- `gateBlock`, `inviteFor` and `parseOperatorDid` are exported, so a host that
  builds its own denial through `gateFor` emits the same block the middleware
  does. `onDeny` now receives the block as a fourth argument.
- `REGISTER_URL`, `CHALLENGE_URL`, `INVITE_SOURCE` and `INVITE_TEXT` are
  exported.
- `test/invite-vectors.json`, 19 vectors, byte-identical to the copy the
  Python package runs.

### Unchanged

- Nothing about verification. A gate without `operatorDid` answers exactly what
  it answered in 2.0.0, with the `moltrustGate` block added alongside the
  existing fields and no `invite` in it.
- The invite never appears on any denial other than `attestation_missing`, and
  never without a well-formed `operatorDid`. There is no fallback referrer.
