-- Auto-Probe-Token: zero-friction onboarding tables.
-- Per spec: docs/auto-probe-token-spec.md §4.1, §9, §13.
--
-- Three new tables, no changes to existing tables in this migration.
-- Companion migration (2026-05-11_agents_probe_parent.sql) adds parent_probe_did
-- to agents and is run after this one.

BEGIN;

-- probe_agents: ephemeral DIDs auto-minted on keyless MCP connections.
-- Kept separate from `agents` so they cannot leak into trust graph queries.
CREATE TABLE IF NOT EXISTS probe_agents (
    did                    text PRIMARY KEY,
    probe_key_hash         text NOT NULL UNIQUE,
    created_at             timestamptz NOT NULL DEFAULT now(),
    expires_at             timestamptz NOT NULL,
    call_count             int NOT NULL DEFAULT 0,
    call_cap               int NOT NULL DEFAULT 50,
    ttl_extensions         int NOT NULL DEFAULT 0,
    first_seen_ip          inet,
    first_seen_ua          text,
    smithery_session_hash  text,
    claimed_at             timestamptz,
    claimed_did            text,
    claimed_email_hash     text,
    CONSTRAINT probe_did_format CHECK (did ~ '^did:moltrust:probe:[0-9a-f]{8}$'),
    CONSTRAINT probe_call_cap_positive CHECK (call_cap > 0),
    CONSTRAINT probe_ttl_extensions_bounded CHECK (ttl_extensions BETWEEN 0 AND 2)
);

CREATE INDEX IF NOT EXISTS idx_probe_active
    ON probe_agents (expires_at)
    WHERE claimed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_probe_ip_recent
    ON probe_agents (first_seen_ip, created_at);

CREATE INDEX IF NOT EXISTS idx_probe_smithery_session
    ON probe_agents (smithery_session_hash)
    WHERE smithery_session_hash IS NOT NULL;

-- probe_activity: per-probe tool call log. Args are redacted of PII before write.
-- Auto-GC drops this alongside the parent probe row.
CREATE TABLE IF NOT EXISTS probe_activity (
    id              bigserial PRIMARY KEY,
    probe_did       text NOT NULL REFERENCES probe_agents(did) ON DELETE CASCADE,
    tool_name       text NOT NULL,
    args_redacted   jsonb,
    result_summary  text,
    at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_probe_act_did ON probe_activity (probe_did, at DESC);
CREATE INDEX IF NOT EXISTS idx_probe_act_tool ON probe_activity (tool_name, at DESC);

-- conversion_funnel: analytics row per probe, lifecycle state + cross-vertical breadth.
-- Survives claim (claim_state flips), GC'd with parent probe row.
CREATE TABLE IF NOT EXISTS conversion_funnel (
    probe_did          text PRIMARY KEY REFERENCES probe_agents(did) ON DELETE CASCADE,
    source             text,
    first_tool         text,
    tool_count         int NOT NULL DEFAULT 0,
    unique_tools       int NOT NULL DEFAULT 0,
    verticals_touched  int NOT NULL DEFAULT 0,
    claim_state        text NOT NULL DEFAULT 'unclaimed',
    claimed_at         timestamptz,
    CONSTRAINT funnel_claim_state_valid CHECK (
        claim_state IN ('unclaimed', 'claimed', 'anonymous-claimed', 'expired')
    )
);

CREATE INDEX IF NOT EXISTS idx_funnel_source     ON conversion_funnel (source);
CREATE INDEX IF NOT EXISTS idx_funnel_state      ON conversion_funnel (claim_state);
CREATE INDEX IF NOT EXISTS idx_funnel_claimed_at ON conversion_funnel (claimed_at) WHERE claimed_at IS NOT NULL;

COMMIT;
