# ES256 (P-256) next to Ed25519

Status: prepared, flag off. KEY-AGILITY, 20.11.2026.

## Why

Some interfaces refuse Ed25519. AWS Marketplace x402 quotes accept only EC
keys (P-256/384/521), OpenID4VC HAIP 1.0 requires ES256 as the minimum, ANS v2
emits only ES256. Others accept only Ed25519 (Cloudflare Web Bot Auth,
Baselayer KYA). MolTrust therefore keeps Ed25519 as the default and adds one
P-256 key for the interfaces that need it.

## What exists

| Piece | Where | Default |
|---|---|---|
| P-256 key, kid `moltrust-es256-2026-v1` | `app/es256_keys.py` | off |
| EC-only key set | `GET /.well-known/jwks-es256.json` (`app/es256_routes.py`) | 404 |
| `#key-p256` Multikey in `did:web:api.moltrust.ch` | `app/main.py:_build_issuer_did_document` | absent |
| Gate verification of ES256 attestations | `packages/x402`, `sdk/python/src/moltrust_enforce/gate.py` | accepts when the JWKS carries an EC P-256 key |

Nothing signs with ES256 today. The main `/.well-known/jwks.json` (static, web
root) is not touched; adding the EC key there is a production change.

## Switching it on

1. Generate a P-256 private scalar (32 bytes, hex) on the server. Human-gated.
2. `MOLTRUST_ES256_PRIVATE_KEY=<hex>` and `KEY_AGILITY_ES256_ENABLED=true` in
   `~/.moltrust_secrets`, restart the API.
3. Check `/.well-known/jwks-es256.json` (200, one EC key) and
   `/.well-known/did.json` (`#key-p256` after the Ed25519 methods).
4. The keys-symmetry check compares did.json kids with jwks.json: add the EC JWK
   to the web-root jwks.json in the same step, or it reports `#key-p256`.

Flag on with a missing or malformed key: the JWKS route answers 503
`es256_key_unavailable`, the DID document omits `#key-p256`. No fallback to
the Ed25519 key.

## Rotation

New scalar under a new kid (`moltrust-es256-YYYY-vN`), published next to the
old one in `jwks-es256.json` (and in the AWS seller account if it is
registered there). The old kid stays until nothing signed with it is still
valid; AWS quotes live at most 300 s. Recorded in `config/key_purposes.json`,
purpose `registry-es256`.

## Not in scope yet

Agent-side P-256 keys (registration, rotation, request signatures): the
`public_key_hex varchar(64)` columns and the 32-byte checks in
`keyless_register.py`, `key_rotation.py`, `did_key.py` and
`did_derivation.py` assume Ed25519. VC proofs (`app/crypto/hybrid.py`) are
held by PR #691. moltguard's vendored gate and `@moltrust/verify` follow in
their own repositories.
