-- PR #204: enforce one Ed25519 key -> one agent DID for keyless PoP registration.
--
-- Partial unique index: only non-NULL public_key_hex is constrained, so legacy
-- and keyed-signup agents (which have no bound key) stay exempt and multiple
-- NULLs are allowed.
--
-- Pre-existing data note: a test-harness fixture registered the same key on two
-- DIDs (did:moltrust:te5tharne550001 / 7e57da001e550001, 2026-04-19/20). The
-- dedup step keeps the earliest row's key and NULLs the rest so the index can
-- build. Verified: that is the only non-NULL duplicate in the table.

BEGIN;

WITH ranked AS (
  SELECT did,
         row_number() OVER (
           PARTITION BY public_key_hex ORDER BY created_at ASC, did ASC
         ) AS rn
  FROM agents
  WHERE public_key_hex IS NOT NULL
)
UPDATE agents a
SET public_key_hex = NULL
FROM ranked r
WHERE a.did = r.did AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_public_key_hex_unique
  ON agents (public_key_hex)
  WHERE public_key_hex IS NOT NULL;

COMMIT;
