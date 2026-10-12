-- MolTrust Ads T4 (2026-10-12). Additive and idempotent; app startup runs the
-- same statements (app/billing.py ensure_billing_tables, app/ads_plan.py
-- ensure_ads_tables). Both tables are owned by the moltstack role.

-- Seats bought on a subscription (Ads Pro: number of ad accounts).
ALTER TABLE billing_subscriptions ADD COLUMN IF NOT EXISTS quantity INTEGER;

-- Ads verdicts per API key (SHA-256 fingerprint, never the key) per UTC month.
CREATE TABLE IF NOT EXISTS ads_verdict_meter (
    key_fp     TEXT        NOT NULL,
    month_key  TEXT        NOT NULL,
    verdicts   BIGINT      NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key_fp, month_key)
);
