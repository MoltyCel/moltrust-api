-- Where a registration came from, in a table this role owns.
--
-- `agents` is postgres-owned and cannot take a new column, so the channel lives
-- beside it. `platform` already exists and is self-reported by the caller; it
-- says which ecosystem an agent belongs to, not which of our surfaces sent it.
-- A bounty agent and an agent that followed a 402 hint both arrive with
-- platform='taskmarket' if they choose to.
--
-- Written once at registration and never updated: a source that can be revised
-- is an attribution nobody can audit.

CREATE TABLE IF NOT EXISTS agent_source (
    did         varchar PRIMARY KEY,
    source      varchar NOT NULL,
    surface     varchar,          -- which unauth response carried the hint
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agent_source_source_idx ON agent_source (source, recorded_at);

COMMENT ON TABLE agent_source IS
  'Channel a registration is attributed to. Role-owned side table; agents is postgres-owned.';
