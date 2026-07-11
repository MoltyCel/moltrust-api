-- Email-path Sybil cost: the credit-granting /identity/register + /auth/signup
-- flow previously had no per-IP / per-domain cost and only exact-string email
-- dedup (defeated by plus-aliasing and gmail dot-tricks). One account minted 2
-- credited DIDs in 40s. This adds:
--
--  * api_keys.email_normalized       -> normalized-email dedup at signup
--  * email_path_registrations table  -> per-domain DID cap (Option A)
--
-- The per-/24 IP cap (Option B) reads the existing agents.registration_ip +
-- idx_agents_reg_ip (SELECT only). The agents table is postgres-owned, so this
-- role cannot ALTER it — the per-domain tracking therefore lives in a dedicated
-- role-owned table rather than a new agents column.
--
-- No UNIQUE constraint on email_normalized: legacy rows may collide after
-- backfill and the app dedups going forward; a unique index would fail the
-- migration on pre-existing duplicates.

BEGIN;

-- 1) Normalized-email dedup key on the (role-owned) api_keys table.
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS email_normalized text;

CREATE INDEX IF NOT EXISTS idx_api_keys_email_normalized
  ON api_keys (email_normalized)
  WHERE email_normalized IS NOT NULL;

-- Backfill email_normalized to match app-side _normalize_email():
--   lowercase, strip +tag from local part, drop dots in local part for gmail.
UPDATE api_keys SET email_normalized =
  CASE
    WHEN lower(split_part(email, '@', 2)) IN ('gmail.com', 'googlemail.com')
      THEN replace(split_part(split_part(lower(email), '@', 1), '+', 1), '.', '') || '@gmail.com'
    ELSE split_part(split_part(lower(email), '@', 1), '+', 1) || '@' || lower(split_part(email, '@', 2))
  END
WHERE email_normalized IS NULL AND email LIKE '%@%';

-- 2) Abuse-tracking rows for the per-domain / per-IP registration gates.
--    One row per credited (keyed) registration.
CREATE TABLE IF NOT EXISTS email_path_registrations (
    did             text PRIMARY KEY,
    email_domain    text,
    registration_ip text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_epr_domain_created
  ON email_path_registrations (email_domain, created_at)
  WHERE email_domain IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_epr_ip_created
  ON email_path_registrations (registration_ip, created_at);

COMMIT;
