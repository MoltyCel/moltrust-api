# UCAN 0.10.0 — Spec-Fakten (Sprint 2 verification gate)

**Spec:** UCAN (User-Controlled Authorization Networks) **v0.10.0** — the JWT model.
**Source:** `https://raw.githubusercontent.com/ucan-wg/spec/v0.10.0/README.md` (+ `github.com/ucan-wg/spec`). **Retrieved:** 2026-07-11.

**Version-pin rationale:** two incompatible spec lines exist. **0.10.0** = self-contained JWT with
`iss/aud/exp/nbf/nnc/fct/cap/prf`. **1.0.0** (current `main`) abandons the monolithic JWT for a
DAG-CBOR envelope with a `sub/cmd/args/policy` model — a rewrite, not a rename. `POST /delegation/create`
mints a **JWT** signed Ed25519-over-`header.payload`, so **0.10.0 is the correct pin**. (`att`/`with`/`can`
belong to the even-older 0.8.x line — NOT used here; 0.10.0 uses `cap`.)

## Token structure (0.10.0)
- **Header:** `{"alg":"EdDSA","typ":"JWT"}`. `alg:"none"` MUST NOT be supported. `ucv` is a **payload** field in 0.10.0 (not header).
- **Payload keys:** `ucv` (String, req — e.g. "0.10.0"), `iss` (DID, req), `aud` (DID, req), `nbf` (int, opt), `exp` (int|null, req), `nnc` (String, opt — note `nnc` not `nonce`), `fct` ({String:Any}, opt), `cap` ({URI:{Ability:[Caveat]}}, req — note `cap` not `att`), `prf` ([CID], opt).
- **Capabilities:** nested map `{ resource_uri: { ability: [caveats] } }`. Example `{"example://x/photos/":{"crud/read":[{}]}}`. `[{}]` = allowed, no restriction; `[]` = disallowed.

## Attenuation (verbatim)
- "each capability delegation MUST have equal or narrower capabilities from its proofs."
- "every unique delegation MUST have equal or narrower capabilities from their delegator." (except rights amplification)
- Caveat array is **disjunctive (OR)**; `[]` disallows, `{}` = no restriction. Escalation `[x]→[{}]` invalid; narrowing `[{}]→[x]` valid.

## Proof chain rules (verbatim)
- Each capability MUST be root (originated by `iss`) OR backed by ≥1 proof in `prf`.
- **iss/aud alignment:** "the `aud` field of every proof MUST match the `iss` field of the outer UCAN … MUST form a chain back to the originating principal."
- **Time-bound nesting:** "All proofs MUST contain time bounds equal to or broader than the UCAN being delegated." Proof expiring before / starting after the outer token → invalid.
- Audience-at-use: executing agent's DID MUST equal outermost `aud`.

## Signature
- EdDSA/Ed25519 (RFC 8037). Signing input = `base64url(header) + "." + base64url(payload)`; signature is the 3rd JWT segment. "The token MUST be signed with the private key associated with the DID in the `iss` field."

## verify MUST-enforce checklist
1 header typ=JWT, alg=EdDSA (reject none) · 2 Ed25519 sig over b64(header).b64(payload) via iss key · 3 required fields ucv/iss/aud/exp/cap · 4 time bounds nbf/exp · 5 aud==executor · 6 each cap root-or-proof-backed · 7 resolve+recurse each proof · 8 proof.aud==outer.iss chain · 9 time-nesting proofs ⊇ outer · 10 attenuation equal-or-narrower · 11 caveat OR / `[]` disallow / `{}` no-restriction · 12 verifying key == iss.

## MolTrust implementation decisions (honest deviations, deliberate)
- **Signer:** MolTrust registry Ed25519 key (`did:web:api.moltrust.ch`, published JWKS at `/.well-known/jwks.json`) signs all minted tokens → verifiable by any JOSE lib against our JWKS. `iss` = `did:web:api.moltrust.ch`; the delegating agent DID is carried in `fct.delegator` (MolTrust acts as the attesting authority). Chain `iss/aud` alignment is evaluated on `fct.delegator`/`aud`.
- **Proofs:** carried as **embedded UCAN JWT strings** in `prf` (self-contained, verifiable without external CID-resolution infra) rather than CIDs. Documented deviation from 0.10.0's `[CID]`; keeps the chain verifiable end-to-end in one call.
- **Policy gate:** `create` is bounded by `agent_delegation_config` (delegation_permitted, max_depth, constraint_mode) of the delegator — configure limits create (Console-Auftrag §3).
- **Revocation:** verify checks `agents.revoked_at` for `fct.delegator`/`aud` DIDs (reuses cascade-revocation state).
- **AAE interaction:** documented in the Sprint-2 PR — `/delegation/*` mint/verify does not itself invoke AAE `evaluate_envelope`; it is an authz-token layer above the enforcement evaluator. No conflict; recorded, not silently resolved (Console-Auftrag §3 DoD).
