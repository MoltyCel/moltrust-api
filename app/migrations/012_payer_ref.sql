-- 012_payer_ref.sql  (Phase 2, 2026-07-16)
-- Payer edge: accounts + payer_ref, agent<->payer side table, subscription link.
-- Additive & reversible. agents is postgres-owned -> NO ALTER on agents;
-- the did<->payer_ref map is the role-owned side table agent_payer.
--
-- ============================ UP ============================

CREATE TABLE IF NOT EXISTS accounts (
    payer_ref               TEXT PRIMARY KEY,
    email                   TEXT,
    stripe_customer_id      TEXT,
    aws_customer_identifier TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_accounts_email           ON accounts(email);
CREATE INDEX IF NOT EXISTS idx_accounts_stripe_customer ON accounts(stripe_customer_id);

CREATE TABLE IF NOT EXISTS agent_payer (
    did        TEXT PRIMARY KEY,
    payer_ref  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_payer_ref ON agent_payer(payer_ref);

CREATE TABLE IF NOT EXISTS payer_usage_meter (
    payer_ref    TEXT NOT NULL,
    did          TEXT NOT NULL,
    calls        BIGINT NOT NULL DEFAULT 0,
    metered_cost BIGINT NOT NULL DEFAULT 0,
    last_call    TIMESTAMPTZ,
    PRIMARY KEY (payer_ref, did)
);
CREATE INDEX IF NOT EXISTS idx_payer_usage_meter_ref ON payer_usage_meter(payer_ref);

ALTER TABLE api_keys              ADD COLUMN IF NOT EXISTS payer_ref TEXT;
ALTER TABLE billing_subscriptions ADD COLUMN IF NOT EXISTS payer_ref TEXT;
CREATE INDEX IF NOT EXISTS idx_billing_sub_payer ON billing_subscriptions(payer_ref);

-- ============================ DOWN ==========================
-- Reversal (run manually to roll back; leaves no orphan objects):
--
-- DROP INDEX IF EXISTS idx_billing_sub_payer;
-- ALTER TABLE billing_subscriptions DROP COLUMN IF EXISTS payer_ref;
-- ALTER TABLE api_keys              DROP COLUMN IF EXISTS payer_ref;
-- DROP TABLE IF EXISTS payer_usage_meter;
-- DROP TABLE IF EXISTS agent_payer;
-- DROP INDEX IF EXISTS idx_accounts_stripe_customer;
-- DROP INDEX IF EXISTS idx_accounts_email;
-- DROP TABLE IF EXISTS accounts;
