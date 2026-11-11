# Changelog

## 2.1.0 — 2026-10-16

### Added

- `moltrust_x402.invite`: `gate_block`, `invite_for` and `parse_operator_did`,
  re-exported from the package root. `gate_block(decision, operator_did)`
  builds the `moltrustGate` block of your 403 — `reason`, `detail`, and
  `invite` where one applies.
- Pass your own `did:moltrust:` identifier and a caller denied with
  `attestation_missing` is invited to register free of charge, with you as the
  referrer. An agent that registers that way is recorded as having come from
  you, and MolTrust issues you a `ReferralCredential` for it.
- `REGISTER_URL`, `CHALLENGE_URL`, `INVITE_SOURCE` and `INVITE_TEXT`.
- `tests/invite-vectors.json`, 19 vectors, byte-identical to the copy the
  TypeScript package runs. Both produce the same JSON for every one of them.

### Changed

- The invitation is this package's own code rather than a re-export. The
  verification core stays what it was: who gets credited for a registration is
  a packaging question, and `moltrust_enforce` has no opinion about it. A test
  asserts those names have not leaked into `moltrust_enforce.gate`.
- `moltrust_enforce.gate.__all__` was missing `check_track_record`, which this
  package has always re-exported. Added, so `from moltrust_enforce.gate import
  *` now reaches it. No behaviour change.

### Unchanged

- Nothing about verification.
- The invite never appears on any denial other than `attestation_missing`, and
  never without a well-formed operator DID. There is no fallback referrer.
