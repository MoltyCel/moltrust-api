-- 2026-09-20_credential_anchoring.sql
-- Anchor columns for issued credentials.
--
-- Until now a VC carried a signature and nothing a third party could point at
-- on chain: /credentials/issue had no anchor path, this table had no column for
-- one, and the only anchoring cron covered IPRs. The funnel test recorded an
-- empty anchoring column for every class and that was accurate.
--
-- Apply by hand, before deploying the code that writes these columns:
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-20_credential_anchoring.sql

BEGIN;

ALTER TABLE credentials ADD COLUMN IF NOT EXISTS anchor_tx_hash TEXT;
ALTER TABLE credentials ADD COLUMN IF NOT EXISTS anchor_block   BIGINT;
ALTER TABLE credentials ADD COLUMN IF NOT EXISTS anchor_status  TEXT;
ALTER TABLE credentials ADD COLUMN IF NOT EXISTS merkle_proof   JSONB;
ALTER TABLE credentials ADD COLUMN IF NOT EXISTS anchored_at    TIMESTAMPTZ;

-- The batch selects on "not yet anchored" every two hours, so that predicate is
-- the one worth an index. Partial, because anchored rows only grow.
CREATE INDEX IF NOT EXISTS idx_credentials_anchor_pending
    ON credentials (issued_at)
    WHERE anchor_status IS DISTINCT FROM 'anchored';

CREATE INDEX IF NOT EXISTS idx_credentials_subject_did
    ON credentials (subject_did);

COMMIT;
