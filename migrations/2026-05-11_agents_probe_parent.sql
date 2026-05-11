-- agents.parent_probe_did: link sub-agents back to their parent probe.
-- Per spec §6.2 (moltrust_register from a probe creates a probe-scoped child).
-- On claim, rows referencing the parent probe are rewritten as legitimate
-- agents bound to the claimed parent DID.

BEGIN;

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS parent_probe_did text REFERENCES probe_agents(did) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_agents_parent_probe
    ON agents (parent_probe_did)
    WHERE parent_probe_did IS NOT NULL;

-- api_keys.tier already exists; extend the implicit value-set with
-- 'anonymous_claimed' for probe-claims that did not provide an email.
-- No DDL needed (tier is plain text), this comment is the audit trail.

COMMIT;
