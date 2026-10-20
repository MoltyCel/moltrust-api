-- Leak canary, token kind (ops LC-4, docs/leak-canary.md "Token canaries").
--
-- A canary is now either a credential (LC-1, looked up by proof_value) or a
-- UCAN delegation token (LC-4, looked up by token_hash). For a token only the
-- hex SHA-256 is stored, never the token. Events and holds are unchanged: a
-- token presentation writes the same event into leak_canary_events.
--
-- Runs after migrations/2026-10-20_leak_canary.sql. Idempotent.
-- Apply by hand: psql -h localhost -U moltstack -d moltstack -f <this file>

ALTER TABLE leak_canaries ADD COLUMN IF NOT EXISTS kind varchar NOT NULL DEFAULT 'credential';
ALTER TABLE leak_canaries ADD COLUMN IF NOT EXISTS token_hash varchar;
ALTER TABLE leak_canaries ALTER COLUMN proof_value DROP NOT NULL;
ALTER TABLE leak_canaries ALTER COLUMN credential_type DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS leak_canaries_token_hash_idx ON leak_canaries (token_hash);

ALTER TABLE leak_canaries DROP CONSTRAINT IF EXISTS leak_canaries_kind_key;
ALTER TABLE leak_canaries ADD CONSTRAINT leak_canaries_kind_key CHECK (
    (kind = 'credential' AND proof_value IS NOT NULL AND credential_type IS NOT NULL
                         AND token_hash IS NULL)
 OR (kind = 'token' AND token_hash ~ '^[0-9a-f]{64}$' AND proof_value IS NULL
                    AND credential_type IS NULL));

COMMENT ON COLUMN leak_canaries.kind IS
  'credential (LC-1, matched on proof_value at POST /credentials/verify) or token (LC-4, matched on token_hash at POST /delegation/verify).';
COMMENT ON COLUMN leak_canaries.token_hash IS
  'Hex SHA-256 of a token canary as issued. The token itself is not stored (ops LC-4).';
