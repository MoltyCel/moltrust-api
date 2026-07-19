-- 2026-07-19_reseller_admin.sql  (Reseller-portal ADMIN access, Phase 2)
-- The single cross-tenant admin role (Lars) for the reseller portal. It bypasses
-- tenant isolation deliberately, so it is gated harder than a reseller login:
--   (1) an existing moltrust.ch/admin session (username/password, app/admin_auth),
--   (2) username on the RESELLER_ADMIN_USERS allowlist (env, fail-closed),
--   (3) a confirmed TOTP second factor, verified at a step-up that mints a
--       short-lived elevated token — no way to reach admin data without TOTP.
--
-- Additive & reversible. All tables moltstack-owned. Idempotent. Mirrored by
-- app/reseller_admin.py:ensure_reseller_admin_tables (up).
--
-- ============================ UP ============================

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- TOTP enrollment, one row per admin username. Secret is ENCRYPTED at rest with
-- pgp_sym_encrypt under RESELLER_ADMIN_TOTP_KEY (env); the app never stores or
-- logs the plaintext secret. `confirmed` gates elevation (a pending secret that
-- was never confirmed by a code cannot elevate).
CREATE TABLE IF NOT EXISTS reseller_admin_2fa (
    username        TEXT PRIMARY KEY,          -- lowercased admin username (matches MOLTRUST_ADMIN_USERS key)
    totp_secret_enc BYTEA NOT NULL,            -- pgp_sym_encrypt(base32_secret, key)
    confirmed       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Elevated step-up sessions: minted ONLY after session+allowlist+valid TOTP.
-- Token hashed at rest (sha256). Short TTL. This is the credential the reseller-
-- admin data/action endpoints require — distinct from the /admin dashboard session.
CREATE TABLE IF NOT EXISTS reseller_admin_sessions (
    token_sha256 TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked      BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_reseller_admin_sessions_user ON reseller_admin_sessions(username);

-- Append-only audit of every admin action (the cross-tenant role logs everything:
-- who, what, when). Content-hash + immutable trigger, like reseller_assignment_audit.
CREATE TABLE IF NOT EXISTS reseller_admin_audit (
    id                BIGSERIAL PRIMARY KEY,
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor             TEXT NOT NULL,           -- admin username
    action            TEXT NOT NULL,           -- '2fa_enroll_start'|'2fa_confirmed'|'elevate'|'create_reseller'|'assign_agent'|'draft_invoice'|...
    target_payer_ref  TEXT,
    detail            JSONB,
    row_hash          TEXT
);
CREATE INDEX IF NOT EXISTS idx_reseller_admin_audit_actor  ON reseller_admin_audit(actor);
CREATE INDEX IF NOT EXISTS idx_reseller_admin_audit_target ON reseller_admin_audit(target_payer_ref);

CREATE OR REPLACE FUNCTION reseller_admin_audit_bind_hash() RETURNS trigger AS $$
BEGIN
  NEW.row_hash := 'sha256:' || encode(
    digest(
      coalesce(to_char(NEW.ts, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), '') || '|' ||
      NEW.actor || '|' || NEW.action || '|' || coalesce(NEW.target_payer_ref, '') || '|' ||
      coalesce(NEW.detail::text, ''),
      'sha256'),
    'hex');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_reseller_admin_audit_bind ON reseller_admin_audit;
CREATE TRIGGER trg_reseller_admin_audit_bind BEFORE INSERT ON reseller_admin_audit
  FOR EACH ROW EXECUTE FUNCTION reseller_admin_audit_bind_hash();

CREATE OR REPLACE FUNCTION reseller_admin_audit_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'reseller_admin_audit is append-only: % forbidden', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_reseller_admin_audit_immutable ON reseller_admin_audit;
CREATE TRIGGER trg_reseller_admin_audit_immutable BEFORE UPDATE OR DELETE ON reseller_admin_audit
  FOR EACH ROW EXECUTE FUNCTION reseller_admin_audit_immutable();

-- ============================ DOWN ==========================
-- Reversal (run manually to roll back):
--
-- DROP TABLE IF EXISTS reseller_admin_audit CASCADE;
-- DROP FUNCTION IF EXISTS reseller_admin_audit_bind_hash();
-- DROP FUNCTION IF EXISTS reseller_admin_audit_immutable();
-- DROP TABLE IF EXISTS reseller_admin_sessions CASCADE;
-- DROP TABLE IF EXISTS reseller_admin_2fa CASCADE;
-- -- pgcrypto stays (shared).
