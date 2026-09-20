-- 2026-09-20_credential_anchoring.sql
-- Anchor records for issued credentials, in a table this role owns.
--
-- `credentials` is owned by postgres: the app role has full DML on it but no
-- DDL, so ALTER TABLE credentials fails with "must be owner of table". Same
-- constraint as `agents`, same answer as PR #237's email_path_registrations —
-- a role-owned side table keyed by the credential id.
--
-- Apply by hand, before deploying the code that writes it:
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-20_credential_anchoring.sql

BEGIN;

CREATE TABLE IF NOT EXISTS credential_anchors (
    credential_id  INTEGER PRIMARY KEY,
    tx_hash        TEXT        NOT NULL,
    block          BIGINT,
    merkle_root    TEXT        NOT NULL,
    merkle_proof   JSONB       NOT NULL,
    anchored_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The batch asks "which credentials have no anchor yet" every two hours, and
-- answers it by anti-joining this table. Anchored rows only accumulate, so the
-- index that matters is the one on the join key — which the primary key already
-- is. This one serves the report query: everything anchored by one transaction.
CREATE INDEX IF NOT EXISTS idx_credential_anchors_tx ON credential_anchors (tx_hash);

COMMIT;
