-- 2026-09-20_agent_profile.sql
-- What we know about an agent beyond the row it registered with.
--
-- `agents` is owned by postgres: this role has full DML on it and no DDL, so
-- ALTER TABLE agents fails with "must be owner of table". Same constraint as
-- credential_anchors and free_tier_state, same answer — a role-owned side table
-- keyed by the DID.
--
-- The table carries two kinds of field and they are deliberately not mixed:
--   declared_*  what the agent said about itself at registration. Unverified by
--               construction; an agent can claim any framework it likes.
--   everything else  what we observed. Derived from request_log and from chain,
--               never from the agent's own assertion.
-- A cluster that mixes the two silently turns a claim into a measurement.
--
-- No IP column, on purpose. Both sources are already truncated to /24 before
-- storage (_anonymize_ip zeroes the last octet; request_log.ip is written the
-- same way), so there is no full address here to protect. Hashing was
-- considered and rejected: a hash over an IPv4 is not anonymisation, because
-- the whole 2^32 space can be enumerated in minutes and the hash walked back.
-- ASN and country are kept in clear because they carry the analysis and neither
-- identifies a person. See docs/agent-profile-privacy.md.
--
-- Apply by hand, before deploying the code that writes it:
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-20_agent_profile.sql

BEGIN;

CREATE TABLE IF NOT EXISTS agent_profile (
    did                  TEXT PRIMARY KEY,

    -- Declared at registration. Optional, unverified.
    declared_capabilities TEXT[],
    declared_description  TEXT,
    agent_card_url        TEXT,
    declared_framework    TEXT,

    -- Observed. Derived, never asserted.
    asn                   TEXT,
    country               TEXT,
    cloud_provider        TEXT,
    ua_framework          TEXT,
    first_endpoints_24h   TEXT[],
    wallet_age_days       INTEGER,
    erc8004_skills        TEXT[],
    a2a_skills            TEXT[],
    taskmarket_tasks      INTEGER,

    -- Provenance of the observation, so a stale or thin profile is visible as
    -- such instead of reading like a fresh one.
    observed_from        TEXT,          -- 'did' | 'registration_ip' | 'none'
    observed_rows        INTEGER,       -- request_log rows the observation rests on
    enriched_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The daily job asks "which profiles are stale or missing"; the cluster queries
-- group by origin and framework. Both are covered here.
CREATE INDEX IF NOT EXISTS idx_agent_profile_enriched ON agent_profile (enriched_at NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_agent_profile_cluster  ON agent_profile (country, ua_framework);

COMMIT;
