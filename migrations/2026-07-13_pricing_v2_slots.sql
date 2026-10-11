-- Pricing v2 slot-enforcement — role-owned side tables (moltstack role).
-- Additive, idempotent, NO behaviour change, NO enforcement. `agents` is
-- postgres-owned and is NOT touched (KB: use role-owned side tables).

CREATE TABLE IF NOT EXISTS agent_environment (
    did          TEXT PRIMARY KEY,
    environment  TEXT NOT NULL DEFAULT 'production'
                 CHECK (environment IN ('production','sandbox','test')),
    set_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS usage_meter (
    did          TEXT NOT NULL,
    month_key    TEXT NOT NULL,          -- 'YYYY-MM'
    endpoint_key TEXT NOT NULL,
    count        BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (did, month_key, endpoint_key)
);
CREATE INDEX IF NOT EXISTS idx_usage_meter_month ON usage_meter (month_key);

CREATE TABLE IF NOT EXISTS slot_shadow_log (
    id                BIGSERIAL PRIMARY KEY,
    day               DATE NOT NULL,
    operator_did      TEXT NOT NULL,
    tier              TEXT,
    slots_counted     INTEGER NOT NULL DEFAULT 0,
    tier_limit        INTEGER,
    would_be_overage  INTEGER NOT NULL DEFAULT 0,
    would_be_blocked  BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (day, operator_did)
);
CREATE INDEX IF NOT EXISTS idx_slot_shadow_day ON slot_shadow_log (day DESC);
