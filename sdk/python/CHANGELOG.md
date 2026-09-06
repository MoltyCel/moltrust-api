# Changelog — moltrust-enforce

Format per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning per
[SemVer](https://semver.org/). Before 1.0.0 a minor version may break; where it does, the
break is listed under **BREAKING** with the migration line next to it.

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
