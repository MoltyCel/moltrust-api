# ADR: PQC dual-signature — capability present, enforcement off by default

**Status:** Accepted (Lars, 2026-07-05)
**Context:** PR #209 added post-quantum dual signatures (Ed25519 + ML-DSA-65) to
the Verifiable-Credential path, initially hard-enforcing that a PQC-capable
issuer must dual-sign.

## Decision

The credential format supports dual signatures (classic Ed25519 + PQC
ML-DSA-65). Enforcement is **not** turned on today. It is gated by a single
central switch, `PQC_ENFORCE` (environment variable), **default off**.

- **Default (off) — advisory:** verification still evaluates the policy and
  surfaces the outcome in the response (`pqc_policy: "satisfied" | "would_reject"`)
  plus a log line, but an Ed25519-only credential from a PQC-capable issuer is
  **accepted**. No existing issuer breaks for lacking a second signature.
- **`PQC_ENFORCE` on — reject:** a PQC-capable issuer's single-signed JCS
  credential is rejected.
- **Legacy** credentials (`sort_keys`, no `canonicalizationAlgorithm`) are always
  exempt — they predate the format and only ever had one leg.

The cryptographic guarantees are unchanged either way: a *dual-signed*
credential still cannot be stripped to Ed25519-only (skeleton binding), both
legs are still verified, `liboqs-python` stays hard-pinned, and the proofValue
length cap stays.

## Rationale

No coercion until there is real need. The capability is prepared and tested;
turning enforcement on is a one-line env flip once the ecosystem (issuers,
wallets) is ready. **No deprecation end-date is set — deliberately open.**

## How to flip

Set `PQC_ENFORCE=true` in the service environment. The switch is read at verify
time.
