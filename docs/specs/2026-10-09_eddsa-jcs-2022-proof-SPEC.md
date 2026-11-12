# Credential proof: eddsa-jcs-2022 (2026-10-09)

## What changes

Credentials issued through `app/credentials.issue_credential` carry one W3C
Data Integrity proof with the `eddsa-jcs-2022` cryptosuite when Dilithium is
not configured (the production state). With Dilithium configured the
composite scheme (`Ed25519Signature2020`-labelled leg + `DilithiumSignature2026`
over a shared proof skeleton) stays as it is: no standard cryptosuite binds an
ML-DSA leg to an Ed25519 one.

| proof field | until 2026-10-09 | from 2026-10-09 |
|---|---|---|
| `type` | `Ed25519Signature2020` | `DataIntegrityProof` |
| `cryptosuite` | — | `eddsa-jcs-2022` |
| `canonicalizationAlgorithm` | `JCS` (absent on 85 older credentials) | — (removed) |
| `created` | the credential's `validFrom` | signing time, UTC, seconds |
| `verificationMethod` | `did:web:api.moltrust.ch#key-ed25519` | unchanged |
| `proofPurpose` | `assertionMethod` | unchanged |
| `@context` | — | the document's `@context` (as the reference implementation writes it) |
| `proofValue` | 128 hex characters | multibase base58btc, `z…` |
| signed bytes | JCS of the document with a proof skeleton (proofValue blank) | `sha256(JCS(proof config)) ‖ sha256(JCS(document without proof))` |

## Why

From 2026-03-10 the Ed25519 proof was labelled `Ed25519Signature2020`, a suite
that canonicalizes with RDF Dataset Canonicalization, while it was signed over
JCS with a hex signature. Only `verify_credential` could check it; any
standard verifier for that type fails.

## Interop evidence

`tests/vectors/eddsa_jcs_2022_vector.json` was produced by Digital Bazaar
`@digitalbazaar/vc` 7.3.0 + `data-integrity` 2.5.0 +
`eddsa-jcs-2022-cryptosuite` 1.0.0 with a fixed seed. Our signer reproduces its
`proofValue` byte for byte and our verifier accepts it
(`tests/test_eddsa_jcs_2022.py`). The DID document needs no change: the same
library verifies against our `Ed25519VerificationKey2020` entry as well as
against a `Multikey` one.

## Verification

`verify_proof` accepts an `eddsa-jcs-2022` proof only as the single proof,
only from an issuer Ed25519 key id, only with `proofPurpose: assertionMethod`
and a proof `@context` equal to the document's. The PQC policy applies as to
an Ed25519-only JCS credential (advisory, rejected under `PQC_ENFORCE`).
Credentials issued before keep verifying through the existing paths.

## Not covered here

- The `evidence` block that `app/provenance/anchor.py` writes into the stored
  credential after signing. It is not signed, so any verifier — ours included —
  rejects a stored credential that carries it. Separate decision.
- Proofs built elsewhere in `app/main.py` (violation records, music
  credentials, attestations) still use the old label.
