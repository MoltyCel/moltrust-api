-- Sprint 1 (feat/compliance-core): EU AI Act assessment history.
-- Additive + idempotent: safe to re-run; no drops, so code-rollback needs no DB rollback.
CREATE TABLE IF NOT EXISTS compliance_assessments (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    did            TEXT NOT NULL,
    risk_tier      TEXT NOT NULL,
    use_case       TEXT,
    intended_purpose TEXT,
    result         JSONB NOT NULL,
    created_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_compliance_assessments_did
    ON compliance_assessments (did, created_at DESC);
