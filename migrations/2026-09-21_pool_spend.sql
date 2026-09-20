-- What each pool cost.
--
-- Acquisition spend is otherwise only recoverable by reading chain history and
-- remembering which transaction belonged to which campaign. One row per
-- payment, written when the payment is made, so cost per registration is a
-- lookup rather than an archaeology exercise.
--
-- tx_hash is unique where present: a bounty escrow recorded twice would halve
-- the apparent cost per registration, which is the one number this table
-- exists to get right. It stays nullable because not every cost is a
-- transaction — a listing fee paid off-chain is still spend.

CREATE TABLE IF NOT EXISTS pool_spend (
    id          BIGSERIAL PRIMARY KEY,
    pool        TEXT           NOT NULL,
    usdc        NUMERIC(18, 6) NOT NULL CHECK (usdc > 0),
    tx_hash     TEXT,
    purpose     TEXT           NOT NULL,
    tranche     INTEGER,
    spent_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),
    recorded_at TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pool_spend_tx
    ON pool_spend (lower(tx_hash)) WHERE tx_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pool_spend_pool_time
    ON pool_spend (pool, spent_at);
