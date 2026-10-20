-- The LinkedIn OAuth state and token, in the database rather than in a file.
--
-- The first version wrote data/linkedin_token.enc and a state file, and the API
-- service answered 500: it runs on a read-only filesystem by systemd
-- hardening, and that is a property worth keeping rather than loosening for one
-- endpoint. A web-facing process keeps its state where it already has write
-- access.
--
-- It is also the better store for this: the callback writes it, the daily
-- renewal cron reads and writes it, and a file with two writers is the shape
-- that has cost us three incidents. One row, owned by the moltstack role (the
-- documented pattern — the postgres-owned tables cannot be altered by us).
--
-- The token is encrypted before it arrives here. The column is bytea and the
-- database never sees the clear text, so a dump or a replica carries ciphertext
-- only; the key lives in ~/.moltrust_secrets and the two are together only in
-- the process that needs them.

CREATE TABLE IF NOT EXISTS linkedin_oauth (
    id          smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    state       text,
    state_at    timestamptz,
    token_enc   bytea,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- One row, enforced by the primary key and the check. An upsert on id = 1 is
-- the only write shape, so two concurrent callbacks cannot leave two tokens.
INSERT INTO linkedin_oauth (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE linkedin_oauth IS
  'LinkedIn OAuth: single row. token_enc is Fernet ciphertext, key derived from '
  'LINKEDIN_CLIENT_SECRET — the database never holds the clear token. '
  'See app/linkedin_oauth.py.';
