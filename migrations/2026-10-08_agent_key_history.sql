-- Retired keys of an agent, method specification section 4.3.
--
-- `agents` is postgres-owned and cannot take a new column, so the history lives
-- beside it in a table this role owns. The key in force stays in
-- agents.public_key_hex; a rotation moves it here, marked with the time it was
-- retired and the signature that retired it.
--
-- app.main.ensure_key_history_table creates the same table at startup; this
-- file is the record of it and the form to apply by hand.

CREATE TABLE IF NOT EXISTS agent_key_history (
    did                varchar NOT NULL,
    key_index          integer NOT NULL,
    public_key_hex     varchar(64) NOT NULL,
    added_at           timestamptz,
    revoked_at         timestamptz NOT NULL DEFAULT now(),
    rotation_signature text NOT NULL,
    PRIMARY KEY (did, key_index)
);

CREATE INDEX IF NOT EXISTS agent_key_history_key_idx ON agent_key_history (public_key_hex);

COMMENT ON TABLE agent_key_history IS
  'Retired Ed25519 keys per DID (section 4.3). Role-owned side table; agents is postgres-owned.';
