-- Backfill evidence[0].credentialId on already-anchored credentials.
--
-- The Merkle leaf preimage starts with the credential's numeric id
-- (docs/spec-fakten/anchor-commitment.md). The credential document never
-- carried it, so a holder could replay the stored sibling path but could not
-- recompute the leaf the path starts from — the leaf was our assertion, not
-- their check. anchor_credentials_batch() now writes the field; this brings
-- the existing rows up to the same shape.
--
-- Additive and idempotent: only rows whose first evidence entry lacks the key
-- are touched, and nothing that feeds a leaf is modified, so no root changes.
-- DML on a postgres-owned table, which the app role does have.

UPDATE credentials
   SET raw_vc = jsonb_set(raw_vc, '{evidence,0,credentialId}', to_jsonb(id), true)
 WHERE raw_vc IS NOT NULL
   AND jsonb_typeof(raw_vc -> 'evidence') = 'array'
   AND jsonb_array_length(raw_vc -> 'evidence') > 0
   AND NOT (raw_vc -> 'evidence' -> 0 ? 'credentialId');

-- Expect: as many rows as there are anchored credentials (261 on 2026-09-21).
SELECT COUNT(*) AS with_credential_id
  FROM credentials
 WHERE raw_vc -> 'evidence' -> 0 ? 'credentialId';
