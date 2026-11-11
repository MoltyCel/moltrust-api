-- Ops V6 (2026-10-18): proof of possession for a DID on /vc/aae/submit and
-- /vc/aae/evaluate, as an HTTP Message Signature (RFC 9421).
-- app/enforcement/request_signature.py is the reader and writer of both.
--
-- request_signature_nonces: a nonce is accepted once per key while its
-- signature is fresh. Rows expire with the freshness window and are purged on
-- the next check.
--
-- aae_evaluations.request_proof: the proof the decision rests on, kept in the
-- record so a third party can check it without the agents table (signature,
-- Signature-Input, keyid, public key, signature base, request body). NULL for
-- an evaluation authenticated by API key. aae_evaluations belongs to this role,
-- so the column can be added; the table stays append-only.
--
-- Additive and idempotent. Apply before the deploy that carries the code.

CREATE TABLE IF NOT EXISTS request_signature_nonces (
    nonce_hash  text PRIMARY KEY,           -- sha256(keyid || '\n' || nonce), hex
    keyid       text NOT NULL,
    expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_request_signature_nonces_expires
    ON request_signature_nonces (expires_at);

ALTER TABLE aae_evaluations ADD COLUMN IF NOT EXISTS request_proof jsonb
    CHECK (request_proof IS NULL OR jsonb_typeof(request_proof) = 'object');

-- DOWN (manual):
--   ALTER TABLE aae_evaluations DROP COLUMN IF EXISTS request_proof;
--   DROP TABLE IF EXISTS request_signature_nonces;
