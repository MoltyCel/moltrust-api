-- 2026-10-16_reseller_suspend.sql  (ops job RES-SUSP)
-- Reseller suspension + reseller applications intake.
--
-- 1. reseller_accounts gets an explicit lifecycle state next to the existing
--    `active` flag. `active` is left untouched for backward compatibility (it
--    stays the "account exists and is enabled" switch); `status` is the new
--    commercial state. Login and every session lookup require
--    active = true AND status = 'active'. Suspension never deletes or rewrites
--    agent_payer rows, agents, audit rows or master data: it flips `status`,
--    stamps `suspended_at` / `suspend_reason`, revokes open sessions
--    (revoked = true, not DELETE) and writes a reseller_admin_audit row.
-- 2. reseller_applications stores applications submitted via
--    POST /reseller/apply. An application never creates an accounts or
--    reseller_accounts row; onboarding stays manual (scripts/create_reseller.py).
--
-- Additive & reversible. reseller_accounts is moltstack-owned -> ALTER allowed.
-- Idempotent (IF NOT EXISTS / guarded constraint). Mirrored by
-- app/reseller.py:ensure_reseller_tables (up).
--
-- ============================ UP ============================

ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;
ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS suspend_reason TEXT;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reseller_accounts_status_chk') THEN
    ALTER TABLE reseller_accounts
      ADD CONSTRAINT reseller_accounts_status_chk CHECK (status IN ('active', 'suspended'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS reseller_applications (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    company              TEXT NOT NULL CHECK (length(company) BETWEEN 2 AND 200),
    contact_name         TEXT NOT NULL CHECK (length(contact_name) BETWEEN 2 AND 120),
    email                TEXT NOT NULL CHECK (length(email) <= 254),
    product_description  TEXT NOT NULL CHECK (length(product_description) BETWEEN 20 AND 4000),
    expected_agent_count INTEGER NOT NULL CHECK (expected_agent_count BETWEEN 1 AND 1000000),
    ip_hash              TEXT,                  -- sha256 of the /24 (/64) anonymized source IP, never the raw IP
    status               TEXT NOT NULL DEFAULT 'new'
                         CHECK (status IN ('new', 'reviewed', 'accepted', 'rejected')),
    notified             BOOLEAN NOT NULL DEFAULT false   -- Telegram notification delivered
);
CREATE INDEX IF NOT EXISTS idx_reseller_applications_ts ON reseller_applications (ts DESC);

-- =========================== DOWN ===========================
-- Drops only what UP added. WARNING: dropping `status` lifts every suspension
-- (a suspended reseller could log in again) and discards stored applications.
-- The suspend/reactivate history survives in reseller_admin_audit.
--
-- DROP TABLE IF EXISTS reseller_applications;
-- ALTER TABLE reseller_accounts DROP CONSTRAINT IF EXISTS reseller_accounts_status_chk;
-- ALTER TABLE reseller_accounts DROP COLUMN IF EXISTS suspend_reason;
-- ALTER TABLE reseller_accounts DROP COLUMN IF EXISTS suspended_at;
-- ALTER TABLE reseller_accounts DROP COLUMN IF EXISTS status;
