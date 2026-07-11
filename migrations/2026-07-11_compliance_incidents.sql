-- Sprint 3 (feat/anchors-incidents): Art 73 serious-incident recording.
-- Additive + idempotent; no drops → code-rollback needs no DB rollback.
CREATE TABLE IF NOT EXISTS compliance_incidents (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    did                TEXT NOT NULL,
    category           TEXT NOT NULL,
    severity           TEXT NOT NULL,
    description        TEXT,
    awareness_date     TIMESTAMP NOT NULL,
    reporting_deadline TIMESTAMP NOT NULL,
    deadline_days      INTEGER NOT NULL,
    art73_rule         TEXT NOT NULL,
    created_at         TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_compliance_incidents_did
    ON compliance_incidents (did, reporting_deadline);
