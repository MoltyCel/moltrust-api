-- 2026-07-18_reseller_portal.sql  (Reseller portal, Phase 2)
-- Multi-tenant reseller portal: a reseller is a payer (accounts.payer_ref) marked
-- as reseller, with its own password login and an assigned wholesale price. The
-- reseller onboards its customer agents self-service by binding a DID to its
-- payer_ref. DID<->payer uniqueness is ALREADY enforced globally by
-- agent_payer.did being PRIMARY KEY (012_payer_ref) — the onboarding path reuses
-- that table, so no second uniqueness domain is introduced here.
--
-- Additive & reversible. accounts is moltstack-owned -> FK allowed; agents is
-- postgres-owned and is NOT touched. Idempotent (IF NOT EXISTS / OR REPLACE /
-- DROP-before-CREATE). Mirrored by app/reseller.py:ensure_reseller_tables (up).
--
-- ============================ UP ============================

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- Reseller marker + credentials + assigned wholesale price. One row per reseller
-- payer_ref. currency is pinned to EUR (single-currency reseller record, per brief).
CREATE TABLE IF NOT EXISTS reseller_accounts (
    payer_ref             TEXT PRIMARY KEY REFERENCES accounts(payer_ref),
    login                 TEXT UNIQUE NOT NULL,           -- stored lowercased
    password_hash         TEXT NOT NULL,                  -- bcrypt ($2b$...), never plaintext
    display_name          TEXT,
    wholesale_price_cents INTEGER NOT NULL CHECK (wholesale_price_cents >= 0),  -- EUR minor units (400 = EUR 4.00)
    currency              TEXT NOT NULL DEFAULT 'EUR' CHECK (currency = 'EUR'),
    customer_vat_id       TEXT,                            -- recipient USt-IdNr (reverse-charge invoicing); required to finalize an invoice
    active                BOOLEAN NOT NULL DEFAULT true,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Additive for installs created before customer_vat_id existed:
ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS customer_vat_id TEXT;

-- DB-backed sessions: opaque bearer token hashed at rest (never store the token
-- itself). Survives restart and works across workers, unlike the in-memory admin
-- SESSIONS dict. Tenant identity = payer_ref resolved from the token per request.
CREATE TABLE IF NOT EXISTS reseller_sessions (
    token_sha256 TEXT PRIMARY KEY,                        -- sha256 hex of the bearer token
    payer_ref    TEXT NOT NULL REFERENCES reseller_accounts(payer_ref),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked      BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_reseller_sessions_payer ON reseller_sessions(payer_ref);
CREATE INDEX IF NOT EXISTS idx_reseller_sessions_expires ON reseller_sessions(expires_at);

-- Append-only audit of every DID<->reseller assignment attempt (assigned or
-- conflict-rejected). Codebase-native integrity pattern: append-only trigger +
-- per-row content hash (like aae_envelopes) — NOT a prev_hash chain (none exists).
CREATE TABLE IF NOT EXISTS reseller_assignment_audit (
    id        BIGSERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
    payer_ref TEXT NOT NULL,
    did       TEXT NOT NULL,
    action    TEXT NOT NULL,               -- 'assigned' | 'rejected_conflict'
    actor     TEXT,                        -- reseller login or 'admin'
    detail    JSONB,
    row_hash  TEXT                         -- sha256 content hash, set server-side by trigger
);
CREATE INDEX IF NOT EXISTS idx_reseller_audit_payer ON reseller_assignment_audit(payer_ref);
CREATE INDEX IF NOT EXISTS idx_reseller_audit_did   ON reseller_assignment_audit(did);

-- Bind row_hash server-side (no app trust): sha256 over the canonical tuple.
CREATE OR REPLACE FUNCTION reseller_audit_bind_hash() RETURNS trigger AS $$
BEGIN
  NEW.row_hash := 'sha256:' || encode(
    digest(
      coalesce(to_char(NEW.ts, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), '') || '|' ||
      NEW.payer_ref || '|' || NEW.did || '|' || NEW.action || '|' ||
      coalesce(NEW.actor, ''),
      'sha256'),
    'hex');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_reseller_audit_bind ON reseller_assignment_audit;
CREATE TRIGGER trg_reseller_audit_bind BEFORE INSERT ON reseller_assignment_audit
  FOR EACH ROW EXECUTE FUNCTION reseller_audit_bind_hash();

-- Immutability: audit is append-only; UPDATE/DELETE hard-forbidden.
CREATE OR REPLACE FUNCTION reseller_audit_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'reseller_assignment_audit is append-only: % forbidden', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_reseller_audit_immutable ON reseller_assignment_audit;
CREATE TRIGGER trg_reseller_audit_immutable BEFORE UPDATE OR DELETE ON reseller_assignment_audit
  FOR EACH ROW EXECUTE FUNCTION reseller_audit_immutable();

-- ============================ DOWN ==========================
-- Reversal (run manually to roll back; leaves no orphan objects):
--
-- DROP TABLE IF EXISTS reseller_assignment_audit CASCADE;   -- table + indexes + triggers
-- DROP FUNCTION IF EXISTS reseller_audit_bind_hash();
-- DROP FUNCTION IF EXISTS reseller_audit_immutable();
-- DROP TABLE IF EXISTS reseller_sessions CASCADE;
-- DROP TABLE IF EXISTS reseller_accounts CASCADE;
-- -- pgcrypto stays (shared by other components) -> do NOT drop.
