-- 2026-09-20_ipr_immutability.sql
-- Anchored IPR fields become immutable, and every write leaves a trace.
--
-- Why: on 2026-04-20 commit 7f3c4d1 corrected the test-harness DID from
-- "did:moltrust:te5tharne550001" (not valid hex) to
-- "did:moltrust:7e57da001e550001" and applied it to rows that had already been
-- anchored. Three records' leaves stopped reproducing, and because a leaf sits
-- in a Merkle tree, two further records in the same batch lost their proofs
-- without having been touched at all.
--
-- Nothing recorded it. The table has no updated_at, no trigger and no audit
-- table, and request_log only reaches back 30 days. The change was findable
-- solely because the leaf gave it away.
--
-- Apply by hand, before deploying the code that relies on it:
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-20_ipr_immutability.sql

BEGIN;

ALTER TABLE interaction_proof_records ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE interaction_proof_records ADD COLUMN IF NOT EXISTS integrity_status TEXT;
ALTER TABLE interaction_proof_records ADD COLUMN IF NOT EXISTS integrity_note   TEXT;
-- A correction after anchoring becomes a new row that points at the old one.
-- The original stays exactly as it was anchored, because that is what the
-- transaction on chain vouches for.
ALTER TABLE interaction_proof_records ADD COLUMN IF NOT EXISTS supersedes_id   UUID;
ALTER TABLE interaction_proof_records ADD COLUMN IF NOT EXISTS superseded_by   UUID;
ALTER TABLE interaction_proof_records ADD COLUMN IF NOT EXISTS record_version  INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_ipr_integrity ON interaction_proof_records (integrity_status)
    WHERE integrity_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ipr_supersedes ON interaction_proof_records (supersedes_id)
    WHERE supersedes_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ipr_audit (
    id          BIGSERIAL PRIMARY KEY,
    ipr_id      UUID        NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    db_user     TEXT        NOT NULL DEFAULT current_user,
    field       TEXT        NOT NULL,
    old_value   TEXT,
    new_value   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ipr_audit_ipr ON ipr_audit (ipr_id, changed_at DESC);

-- The four fields that go into the Merkle leaf. Change any of them after the
-- record is anchored and the anchor stops covering the record.
CREATE OR REPLACE FUNCTION ipr_guard_anchored_fields() RETURNS TRIGGER AS $$
DECLARE
    f TEXT;
    old_v TEXT;
    new_v TEXT;
BEGIN
    NEW.updated_at := now();

    FOREACH f IN ARRAY ARRAY['output_hash', 'agent_did', 'produced_at', 'confidence'] LOOP
        EXECUTE format('SELECT ($1).%I::text, ($2).%I::text', f, f)
            INTO old_v, new_v USING OLD, NEW;
        IF old_v IS DISTINCT FROM new_v THEN
            IF OLD.anchor_status = 'anchored' THEN
                RAISE EXCEPTION
                    'IPR % is anchored in %; % is part of its Merkle leaf and cannot be changed. Insert a superseding row instead.',
                    OLD.id, COALESCE(OLD.anchor_tx, 'a batch'), f
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            INSERT INTO ipr_audit (ipr_id, field, old_value, new_value)
            VALUES (OLD.id, f, old_v, new_v);
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ipr_guard_anchored_fields ON interaction_proof_records;
CREATE TRIGGER trg_ipr_guard_anchored_fields
    BEFORE UPDATE ON interaction_proof_records
    FOR EACH ROW EXECUTE FUNCTION ipr_guard_anchored_fields();

COMMIT;
