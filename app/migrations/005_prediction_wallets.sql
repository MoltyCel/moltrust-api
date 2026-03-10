-- MolTrust Prediction Markets: Wallet-to-DID Bridge
-- Migration 005: prediction_wallets + prediction_market_events

CREATE TABLE IF NOT EXISTS prediction_wallets (
    id              SERIAL PRIMARY KEY,
    address         TEXT NOT NULL UNIQUE,
    platform        TEXT NOT NULL DEFAULT 'polymarket',
    linked_did      TEXT,
    linked_at       TIMESTAMPTZ,
    total_bets      INT NOT NULL DEFAULT 0,
    wins            INT NOT NULL DEFAULT 0,
    losses          INT NOT NULL DEFAULT 0,
    total_volume    NUMERIC(18,6) NOT NULL DEFAULT 0,
    net_pnl         NUMERIC(18,6) NOT NULL DEFAULT 0,
    prediction_score INT NOT NULL DEFAULT 0,
    score_breakdown JSONB NOT NULL DEFAULT '{}',
    last_synced     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pw_score ON prediction_wallets (prediction_score DESC);
CREATE INDEX IF NOT EXISTS idx_pw_did ON prediction_wallets (linked_did) WHERE linked_did IS NOT NULL;

CREATE TABLE IF NOT EXISTS prediction_market_events (
    id              SERIAL PRIMARY KEY,
    wallet_address  TEXT NOT NULL REFERENCES prediction_wallets(address),
    market_id       TEXT NOT NULL,
    market_question TEXT,
    platform        TEXT NOT NULL DEFAULT 'polymarket',
    outcome         TEXT,
    amount_in       NUMERIC(18,6),
    amount_out      NUMERIC(18,6),
    position        TEXT,
    event_timestamp TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pme_wallet ON prediction_market_events (wallet_address);
CREATE INDEX IF NOT EXISTS idx_pme_market ON prediction_market_events (market_id);
