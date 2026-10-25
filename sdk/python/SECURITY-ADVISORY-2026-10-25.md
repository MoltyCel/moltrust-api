# Security advisory 2026-10-25: self-issued credential types in gate attestations

Published 2026-10-25.

**Service:** `api.moltrust.ch`, `POST /credentials/issue` and gate attestations from `GET /skill/trust-score/<did>`
**Packages:**
- `@moltrust/x402` 2.0.0 (npm), with the `credentialType` or `requiredCredentials` option
- `moltrust-enforce` 0.6.0 to 0.7.1 (PyPI), with `required_credentials`

**Fixed:** 2026-10-25 09:37 UTC, on the service. The packages are unchanged.

## Description

From 2026-03-10 to 2026-10-25 09:37 UTC, `POST /credentials/issue` signed any credential type name that matched `^[a-zA-Z][a-zA-Z0-9]{1,63}$`. The route checked that the caller's API key owned the subject DID, or that the caller held the admin key. It did not check the type. An agent could have the registry (`did:web:api.moltrust.ch`) sign a credential for its own DID under a type name of its choice. The same held for leak canaries (`POST /canary/issue`), which issued the credential for a counterparty DID.

Since 2026-09-21 the gate attestation from `GET /skill/trust-score/<did>` lists every unrevoked, unexpired credential type of the DID in `credential_types`. A gate that requires a credential type checks it against this list. From 2026-09-21 to 2026-10-25 a credential type in this list could therefore be a type that the agent had named itself.

`POST /credentials/verify` checked the signature and did not read revocation. A credential the registry had revoked answered `valid: true`.

## Where this applies

This applies to gates that require a credential type:

- `@moltrust/x402` 2.0.0 with `credentialType` or `requiredCredentials`
- `moltrust-enforce` 0.6.0 to 0.7.1 with `required_credentials`

This applies to any relying party that used `POST /credentials/verify` to decide whether a credential had been revoked.

Gates without a type requirement do not read `credential_types`. This covers gates that check the trust score, a track record, or both.

## Timeline (UTC)

| Date | Event |
|---|---|
| 2026-03-10 | `POST /credentials/issue` accepts a caller-chosen `credential_type`. |
| 2026-09-21 | Gate attestations carry `credential_types`. |
| 2026-09-27 | `moltrust-enforce` 0.6.0 with `required_credentials` is published. |
| 2026-09-28 | `@moltrust/x402` 2.0.0 with `credentialType` and `requiredCredentials` is published. |
| 2026-10-24 03:50–03:57 | Three agents issue nine credentials for their own DIDs: four `AdminTrustCredential`, three `RootCredential`, two `OperatorCredential`. |
| 2026-10-25 09:37 | Fix deployed ([moltrust-api#938](https://github.com/MoltyCel/moltrust-api/pull/938)). |
| 2026-10-25 09:38 | The nine credentials are revoked. |
| 2026-10-25 10:38 | An attestation is valid for one hour. An attestation fetched before the revocation is valid at the latest until this time. |

## Credential types issued from 2026-09-21 to the fix

Seven credential types were issued in this window. Four are the types of their routes: `AgentTrustCredential`, `TrackRecordCredential`, `ReferralCredential` and `SecurityAudit`. The other three are `AdminTrustCredential`, `RootCredential` and `OperatorCredential`, the nine credentials listed above.

Two `SecurityAudit` credentials were issued in the window. For the one from 2026-10-22, the request log has no request from its subject DID. The one from 2026-09-21 is older than the request log, which begins on 2026-09-25.

## Fix

[moltrust-api#938](https://github.com/MoltyCel/moltrust-api/pull/938), deployed 2026-10-25 09:37 UTC:

- `POST /credentials/issue` issues `AgentTrustCredential` for the caller's own DID. With the admin key it also issues `SecurityAudit`. Any other type is refused with HTTP 400 before anything is signed.
- `POST /canary/issue` issues `AgentTrustCredential`.
- `POST /credentials/verify` answers `valid: false`, `revoked: true` and `revoked_at` for a credential the registry has revoked.

The nine credentials were revoked on 2026-10-25 at 09:38 UTC. Attestations fetched since then do not list their types.

## Recommendations for gate operators

- If your gate requires a credential type, check its decisions from 2026-09-28 to 2026-10-25 10:38 UTC for admissions that rested on a credential type.
- Send a credential to `POST /credentials/verify` to see whether it has been revoked. A revoked credential answers `valid: false` with `revoked_at`.
- Fetch a fresh attestation for each decision instead of reusing a stored one. An attestation is valid for one hour.
