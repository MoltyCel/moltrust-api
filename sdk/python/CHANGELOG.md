# Changelog — moltrust-enforce

Format per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning per
[SemVer](https://semver.org/). Before 1.0.0 a minor version may break; where it does, the
break is listed under **BREAKING** with the migration line next to it.

## [Unreleased]

## [0.8.0] — prepared 2026-10-20, not on PyPI

### Added

- **Automatic DID registration, `moltrust_enforce.autoregister`.** An `EnforceClient`
  built without `api_key` and `did` registers a DID for the installation on its first
  `check()` or `ratify()`: Ed25519 key pair generated locally, public key registered
  through `/identity/register-challenge` and `/identity/register-pop` with the platform
  `usage-enforce`, DID and key stored in `~/.moltrust/enforce-identity.json` (mode 0600)
  and sent as `X-MolTrust-DID`. Registration never raises; a failure is one warning and
  the call goes out without a DID. Not run when `CI` is set. Opt out with
  `auto_register=False` or `MOLTRUST_NO_AUTO_REGISTER=1`; then the old `ValueError` for a
  client without credentials stands. README "Automatic DID registration".

- `TelegramApprover`: asks for a hold approval through injected `send`/`wait` callables and
  signs the ratification with the approver's key. The package itself never calls Telegram.
- `ed25519_attester` and `trust_list_for` for the receipt's attestation and its trust list.
- **Deposit constraints, `moltrust_enforce.deposit`.** Five constraint types for Verifiable
  Intent open payment mandates, named per VI §6.1 Model B:
  `ch.moltrust.deposit.min_hold_days`, `.sweep_threshold_bp`, `.max_sweeps_per_period`,
  `.insured_counterparty_only` and `.execution_window_hours`. `check_transfer(mandate,
  transfer, history, bundle)` returns `allow`, `hold` or `forbid` with the reason, in the
  order of the public draft sketch. Amounts are ISO 4217 minor units, periods are ISO 20022
  frequency codes as in VI §4.6 (`INDA` refused). Rates and deposit insurance status come only
  from an AER evidence bundle bound to the mandate and the transfer, judged at the trigger
  time. The kernel reads no clock and opens no connection. `verify_deposit_evidence` checks
  the signatures of that evidence against a pinned trust list (V2 and V3 of
  `verify_record`). `window_offset_seconds(mandate_id, trigger_id, hours)` recomputes the
  execution offset from `sha256(mandate_id|trigger_id)`. `TOLERANCE_SECONDS` is 300.
- Conformance vectors in `tests/deposit-vectors.json`: at least one `allow` and one `forbid`
  case per type, `hold` cases for the execution window, and pinned offsets.
- `docs/deposit-constraints-vi-mapping.md`: each type mapped against VI §3.1, §3.2, §6.1 and
  §6.2 and against AP2, the VI §5.4 consequence (open mandates with unknown types are
  rejected), and every decision beyond the draft schema.
- **Leak canary, `moltrust_enforce.canary`.** `canary.check(token, base_url=..., api_key=...)`
  presents a delegated token at the token endpoint `POST /delegation/verify` and returns its
  answer, fail-closed (`TokenCheck`); `signed_request` forwards a request the holder signed.
  `canary.check(event, trust_list=...)` checks a leak event offline (`LeakEventReport`): the
  event signature under the domain `moltrust:leak-canary-event:v1` 0x00, the AER decision it
  carries (`verify_record`, PERMIT), the binding of the evidence to the event, and for
  attribution `counterparty` the holder's request signature. Events from the verifier and
  from the token endpoint pass the same check. No HTTP client is loaded at import.

### Migration

None for a client constructed with `api_key` or `did`. A client constructed with neither
no longer raises `ValueError`; it registers a DID on its first call unless opted out. `core_digest`, `ENFORCE_VERSION` and `AER_VERSION` are
unchanged. The new modules are additive and import nothing new: they use `httpx` from the
`client` extra and `cryptography` from the base.

## [0.7.1] — 2026-10-18, on PyPI

### Fixed

- **No digest over a rounded integer.** jcs 0.2.1 writes every number through a double,
  so an integer beyond 2**53-1 that is not an exact double came back as its neighbour:
  `9007199254740993` as `9007199254740992`, `10**21+1` as `1e+21`
  ([titusz/jcs#1](https://github.com/titusz/jcs/issues/1)). Two transactions or mandates
  that differed only in such an integer shared one `action_digest`, `transaction_digest`
  and `core_digest`, and `recompute` confirmed one with the record of the other. The
  vendored core and `moltrust_enforce.evidence` now canonicalize through
  `moltrust_enforce._jcs`, which refuses any integer outside +-(2**53-1) (RFC 7493
  section 2.2) before calling jcs; the digest functions return `None` for such input. The
  server does the same since moltrust-api #788/#819, so both reach the same verdict.
- **A digest that cannot be computed is a DENY** (server: moltrust-api #821, #822). An
  input that does not canonicalize (an integer beyond the range, NaN, Infinity, a lone
  surrogate) anywhere in the transaction or the mandate, or in an ancestor mandate, used to
  leave `null` in the core next to a PERMIT. `enforce_check` now answers DENY with a
  `transaction_digest`, `mandate_digest` or `grant_attenuation` predicate, and `recompute`
  returns False whenever a computed digest is missing.

### BREAKING

- Input that 0.7.0 accepted with a rounded or null digest is now refused: `None` from the
  digest functions, DENY from `enforce_check`, False from `recompute`. Integers outside
  +-(2**53-1) belong in strings, as RFC 8785 section 3.1 recommends.

## [0.7.0] — 2026-10-12, on PyPI

### Added

- **Grant attenuation per delegation hop** (AAE -02 §5 step 9). `enforce_check()` and
  `recompute()` take an optional `ancestors` argument: the parent mandates of a delegated
  mandate, root first, at most 8 (`MAX_ANCESTORS`). Each hop is checked and recorded in the
  trace as a `grant_attenuation` predicate. A child grant fails the check when it binds an
  action no parent grant binds, moves a disposition down (`forbid` > `hold` > `allow`),
  keeps an `allow` or `hold` for an action the parent forbids, drops a parent constraint, or
  is an `allow` that an earlier parent `hold` for the same action could intercept.
  `grant_attenuation_problem(child, parent)` in `moltrust_enforce._core` gives the reason
  for one hop. Server change: #598.
- **`moltrust_enforce.adapters.amazon_ads`** — a verdict gate for agents that call the
  Amazon Ads MCP Server. `AdsProfile` (ad accounts, brands, daily and account-wide budget
  caps, hold threshold, period budget cap, bid cap and maximum bid change, validity window)
  becomes a mandate with `build_mandate`. `AmazonAdsGuard.call(tool, args, execute,
  context)` splits the call into one check per changed item, asks `POST /enforce/check`
  for each, and runs the caller's own `execute(tool, args)` only if every check is PERMIT
  or a PENDING that `/enforce/ratify` approved. Deny-by-default; the user-permission,
  user-invitation and manager-account groups can never be granted; Lite `invoke_tool`
  is unwrapped. The package contains no Amazon client and sends nothing to Amazon. Every
  check carries an AER record that `moltrust-verify` recomputes offline. Encoding:
  `docs/spec-fakten/aae-ads-profile.md`. Demo: `examples/amazon_ads/demo.py`.

### Fixed

- `moltrust_enforce.__version__` reported `"0.5.0"` in the 0.6.0 wheel. It now matches the
  package version.

### Migration

None. Without `ancestors` the core and its digest are byte-identical to 0.6.0, and
`ENFORCE_VERSION` stays `"3.0"`. Records from 0.5.0 and 0.6.0 recompute unchanged.

### Known gap

- `EnforceClient.check()` has no `ancestors` parameter yet, so the HTTP client cannot ask
  `POST /enforce/check` for a delegated check. Recomputing such a record locally works.
  Planned for 0.8.0.

## [0.6.0] — 2026-09-27, on PyPI

`moltrust_enforce.gate` — an offline trust gate for a paid endpoint's own door.
`require_moltrust(min_score, credential_type)` verifies a MolTrust-signed
attestation against a cached JWKS and the caller's own Ed25519 signature over
method, path, DID and timestamp. No network call in the request path.

Deny by default, and a withheld score is a denial: a score we have not computed
is not a low score and not a pass. `allow_withheld=True` is the explicit way to
let new agents into a discount tier, and it does not bypass `min_score`.

Needs the `gate_attestation` field on `GET /skill/trust-score/<did>`, which
carries the DID's public key. The v1 `registry_jws` payload does not and is
rejected with a reason — without a signed DID-to-key binding a gate can verify
that a score exists and never that the caller is its subject.

The Node port is `@moltrust/x402` 2.0.0.

## [0.5.0] — 2026-09-04, on PyPI

### BREAKING

- **The digested core carries no free text any more.** `reason` is dropped from all three
  cores — verdict (`aae:enforce-core:v1`), ratification (`aae:enforce-ratify-core:v1`) and
  AER decision (`aae:aer-core:v1`) — and likewise from every predicate entry of the trace
  that sits inside those cores. An entry in the core now carries only `predicate`, `field`,
  `value`, `bound`, `result`.

  **What stays:** `reason` is unchanged in the response, on the verdict as on every
  predicate. Whoever reads the reason keeps reading it. It is simply no longer part of the
  value two implementations have to hit byte for byte — free text whose wording they do not
  have to agree on has no place there. AAE -02 §2.5.2/§2.5.3.

  **Migration:** no `core_digest` from 0.4.0 or earlier can be recomputed against this
  package. `ENFORCE_VERSION`, `RATIFY_VERSION` and `AER_VERSION` are therefore at `"3.0"`
  (previously `"2.0"`) and sit in every core as `enforce_version`, `ratify_version` and
  `aer_version` respectively: whoever holds a record with `2.0` can see from the field that
  they need the old package version.

  Not affected: `action_binding`, `mandate_digest`, `transaction_digest`, the evidence item
  and query digests, and `delegator_aae_hash`. Verdicts and decisions themselves do not
  change — PERMIT stays PERMIT, the guards hold unchanged. What changes is exclusively what
  gets digested.

  The AER core does not come along for `reason` alone: it carries `enforce_version` as a
  field of its own, so the bump would have broken it anyway.

### Changed

- The conformance vectors were regenerated against the 3.0 kernel and released as
  [v1.4.0](https://github.com/MoltyCel/aae-conformance-vectors/releases/tag/v1.4.0). Same 26
  vectors, same inputs, same verdicts and statuses; only the expected core digests move, and
  `kernel_version` goes 2.0 → 3.0 with them. v1.3.0 remains the pre-3.0 state.

## [0.4.0] — on PyPI

### BREAKING

- **The domain tags are vendor-neutral: `moltrust:…` becomes `aae:…`.** This changes
  **every** digest the package emits — `action_binding`, `mandate_digest`,
  `transaction_digest`, `core_digest`, the signed bytes of a ratification and, via
  `mandate_ref`/`transaction_ref`, every AER bundle as well. Ten tags across four modules:

  | Role | before | now |
  |---|---|---|
  | Action, mandate, transaction, core | `moltrust:enforce-*:v1` | `aae:enforce-*:v1` |
  | Ratification statement, core | `moltrust:enforce-ratify-*:v1` | `aae:enforce-ratify-*:v1` |
  | AER item, query, bundle, core | `moltrust:aer-*:v1` | `aae:aer-*:v1` |

  The reason is the IETF draft: AAE -02 fixes the tag values normatively, and a format value
  carrying a company name is out of place in a standard. There is no precedent this breaks —
  the tagged digest construction enters the format with -02 for the first time, `-00` and
  `-01` do not know it.

  **Migration:** a record from 0.1.0–0.3.0 can no longer be recomputed with this package.
  How to tell without guessing: `ENFORCE_VERSION` and `RATIFY_VERSION` are now at `"2.0"`
  (previously `"1.0"`) and sit in every core as `enforce_version` and `ratify_version`.
  Whoever holds a record with `1.0` needs the old package version to check it. Newly issued
  records carry `2.0`.

  `moltrust:aae-verdict:v1` in the server (`verdict_sign.py`) is unchanged: it belongs to the
  AAE evaluator, not to the enforce kernel, appears in no draft sentence, and its signatures
  live in the database.

- **A `prev_core_digest` supplied to `ratify()` must point at the record being ratified.**
  Until now a well-formed but foreign digest was taken at face value; the record then claimed
  a chain that does not exist. Now `RatifyError` — as with a non-ratifiable predecessor,
  because both are caller errors and there is nothing to record when the question itself does
  not line up. Without the argument nothing changes: the core carries the predecessor's
  `core_digest`. AAE -02 §6.2.

- **`httpx` is no longer a base dependency.** The HTTP client sits in the `client` extra.
  Anyone using `EnforceClient` migrates with:

  ```bash
  pip install 'moltrust-enforce[client]'
  ```

  Affected are all four names from `client.py` — `EnforceClient`, `Ratification`, `Verdict`
  and `VerifyResult`. Measured in a subprocess with the httpx import blocked; the names from
  the kernel, the evidence layer and the verifier are not affected
  (`tests/test_aer_verify.py::test_every_client_name_needs_httpx_and_no_other_name_does`).

  Accessing one of the four without the extra does not raise a bare `ModuleNotFoundError`; it
  names the extra and the whole affected set. The reason for the cut: whoever recomputes
  someone else's verdict is not the same party as whoever requests verdicts. A verification
  install comes to 5 packages instead of 12.

  0.1.0, 0.2.0 and 0.3.0 are on PyPI, so the break hits existing installations.

### Added

- **AER — Attested-Evidence Replay**, stages 1 and 4 of the feature spec. The kernel
  additionally decides on live preconditions — revocation state, sanctions and jurisdiction
  status, exchange rate — which sit in the bundle as signed statements with a validity window
  and are kept with the decision.
  - `evidence.py` — evidence item as a DSSE envelope; what is signed is the PAE over
    `(payloadType, payload)`. Bundle ascending by `item_digest`, `bundle_commit` over
    everything but itself, `mandate_ref`/`transaction_ref` under the same domain tags as the
    static kernel. Times as RFC 3339 UTC to whole seconds.
  - `_ext_core.py` — `f_ext(mandate, transaction, bundle)`, pure, no network and no clock.
    Four evidence constraints: `evidence_bool`, `evidence_enum`, `evidence_range` and
    `evidence_scaled_range` (amount times rate against a fiat limit, integral, no rounding).
    Each additionally checks its item's window against the `decision_timestamp`.
  - `verify.py` — V1 integrity, V2 Ed25519 against a supplied trust list, V3 freshness across
    all items, V4 recomputation. All four run even when one of them already fails.
  - `cli.py` — `moltrust-verify` as a console script, network-free, exit 0/1/2.
  - `examples/aer/` — a finished decision as JSON plus trust list, and the deterministic
    generator for it.
- Extra `verify` — empty, so that `pip install 'moltrust-enforce[verify]'` runs and the answer
  to the question about the offline path's dependencies sits in the package itself.

### Changed

- `EnforceClient`, `Ratification`, `Verdict` and `VerifyResult` are loaded on access through a
  module `__getattr__` (PEP 562). `import moltrust_enforce.cli` therefore pulls neither
  `httpx` nor `socket` or `ssl` into the process; what is loaded is `jcs` and `cryptography`.
  The public surface is unchanged.
- `evidence_payload_bytes` is named that and not `statement_bytes` — the ratification kernel
  already uses that name with a different meaning.
- CI job `sdk-tests` installs `[test]`, because the client tests need httpx, which now sits in
  the extra.

### Fixed

- The README line saying the package was not on PyPI was wrong; 0.1.0, 0.2.0 and 0.3.0 are
  there.

## [0.3.0] — PR #319, on PyPI

### BREAKING

- **Every grant needs `type_fields`.** A mandate without the field is invalid, and a
  transaction's `action` has to carry exactly the keys named there — none missing, none
  extra. Instance values such as amount and recipient stay siblings of the action and run
  through constraints. An `action` that is not an object is rejected.

## [0.2.0] — PR #309

- Ratification kernel (`ratify`, `mandate_authorities`, `ratification_statement`) and the
  local check of the ratification signature over Ed25519.

## [0.1.0] — PR #307

- Reference client for `POST /enforce/check` with a local recomputation kernel
  (`enforce_check`, `action_digest`, `core_digest`, `recompute`).
