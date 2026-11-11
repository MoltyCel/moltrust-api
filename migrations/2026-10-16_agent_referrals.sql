-- Who brought whom (ops G2, read by the cohort figures of ops G6).
--
-- `agents` is postgres-owned and cannot take a referrer_did column, so the
-- referral lives beside it in a table this role owns, one row per referred
-- agent. The cohort query in app/sql/registry_export.sql counts rows here per
-- referrer; until register-pop writes them (G2) the count is 0 for everyone,
-- which is the true figure and not a missing one.
--
-- Apply by hand: psql -h localhost -U moltstack -d moltstack -f <this file>

CREATE TABLE IF NOT EXISTS agent_referrals (
    did          varchar PRIMARY KEY,
    referrer_did varchar NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (did <> referrer_did)
);

CREATE INDEX IF NOT EXISTS agent_referrals_referrer_idx ON agent_referrals (referrer_did);

COMMENT ON TABLE agent_referrals IS
  'One row per referred agent: referrer_did named at registration. Role-owned side table; agents is postgres-owned.';
