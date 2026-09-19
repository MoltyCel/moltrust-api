-- 2026-09-19_free_tier_signup_by_signature.sql
-- Free-tier expansion: a second way to mint an API key, and the per-DID state
-- the hourly allowance and the monthly floor are counted in.
--
-- Apply by hand (no in-app runner):
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-19_free_tier_signup_by_signature.sql
--
-- `free_tier_state` is also created at startup by app.free_tier's
-- ensure_free_tier_tables, so a fresh deploy does not depend on this file
-- having been run. The api_keys change does, and has to run BEFORE the new
-- /auth/signup-did endpoint is deployed.

BEGIN;

-- Signature signup has no email to store. The column stays for the email path,
-- which still enforces its own uniqueness through email_normalized; dedup on the
-- signature path is by owner_did instead.
ALTER TABLE api_keys ALTER COLUMN email DROP NOT NULL;

-- How this key was minted. NULL on every existing row, which is what the email
-- path has always meant.
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS signup_method TEXT;

-- One key per agent, but only on the signature path. Scoping the index to
-- signup_method leaves the email path exactly as it is: it has never promised
-- one key per agent, and 67 of the 90 existing keys carry an owner_did that a
-- global unique index would start enforcing retroactively.
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_did_signup_unique
    ON api_keys (owner_did)
    WHERE signup_method = 'did_signature' AND owner_did IS NOT NULL;

CREATE TABLE IF NOT EXISTS free_tier_state (
    did                TEXT PRIMARY KEY,
    hour_window        TIMESTAMPTZ NOT NULL DEFAULT date_trunc('hour', now()),
    calls_this_hour    INTEGER     NOT NULL DEFAULT 0,
    last_floor_month   DATE,
    first_credential_at TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- CREATE TABLE IF NOT EXISTS adds no column to a table that already stands,
-- and app.free_tier creates this table at startup too.
ALTER TABLE free_tier_state ADD COLUMN IF NOT EXISTS first_credential_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_free_tier_state_hour ON free_tier_state (hour_window);

COMMIT;
