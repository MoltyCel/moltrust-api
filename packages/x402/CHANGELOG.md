# Changelog

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
