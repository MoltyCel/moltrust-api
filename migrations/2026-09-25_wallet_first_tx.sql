-- 2026-09-25_wallet_first_tx.sql
-- When a wallet first sent a transaction of its own. Cached, because it never
-- changes and because finding it costs about twenty-five RPC calls.
--
-- The track-record threshold asks two things of a wallet: has it sent anything,
-- and how long has it existed. The first is one call. The second used to come
-- from Blockscout, and a sweep over seventy-eight wallets on 2026-09-25 came
-- back with sixty-eight "not readable" — the explorer throttled, and a throttle
-- reported as a finding is worse than no finding. Under a hundred simultaneous
-- issuances it would have failed the same way, so the answer now comes from the
-- node by binary search over the nonce, and lands here so the next caller pays
-- nothing for it.
--
-- Immutable by nature: a wallet's first transaction cannot move. Rows are
-- written once and never updated, which is also why there is no TTL.
--
-- Role-owned side table, like agent_profile and credential_anchors: the app
-- role has DML and no DDL on the postgres-owned tables.
--
-- Apply by hand, before deploying the code that writes it:
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-25_wallet_first_tx.sql

BEGIN;

CREATE TABLE IF NOT EXISTS wallet_first_tx (
    wallet      TEXT PRIMARY KEY,
    chain       TEXT        NOT NULL DEFAULT 'base',
    -- Null means the search ran and found nothing: the wallet has never sent a
    -- transaction. Stored rather than left absent, so a nonce-0 wallet is not
    -- re-searched on every call.
    first_block BIGINT,
    first_ts    TIMESTAMPTZ,
    -- 'node' when the block was found by binary search over the nonce,
    -- 'node-floor' when the wallet was already active at the start of the
    -- search window, so the age is a lower bound rather than the value.
    source      TEXT        NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
