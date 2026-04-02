-- MolTrust API — Database Schema (dev/test)
-- This matches the columns expected by the current codebase.

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    did TEXT UNIQUE NOT NULL,
    public_key TEXT DEFAULT '',
    display_name TEXT,
    platform TEXT DEFAULT 'moltbook',
    agent_type TEXT DEFAULT 'external',
    created_at TIMESTAMP DEFAULT NOW(),
    reputation_score DECIMAL(5,2) DEFAULT 0.00,
    base_tx_hash TEXT,
    erc8004_agent_id INTEGER,
    wallet_address TEXT,
    wallet_chain TEXT,
    wallet_bound_at TIMESTAMP,
    last_seen TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ratings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_did TEXT NOT NULL,
    to_did TEXT NOT NULL,
    score INTEGER CHECK (score BETWEEN 1 AND 5),
    context TEXT,
    transaction_hash TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_did TEXT NOT NULL,
    credential_type TEXT NOT NULL,
    issuer TEXT NOT NULL,
    issued_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP,
    proof_value TEXT,
    raw_vc TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    developer_agent UUID,
    security_score DECIMAL(3,2),
    price_usdc DECIMAL(10,2),
    price_sats BIGINT,
    repo_url TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_agent UUID,
    skill_id UUID,
    amount DECIMAL(10,2),
    currency TEXT CHECK (currency IN ('USDC', 'BTC')),
    payment_hash TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key TEXT UNIQUE NOT NULL,
    owner_did TEXT NOT NULL,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS credit_balances (
    agent_did TEXT PRIMARY KEY,
    balance INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS usdc_deposits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tx_hash TEXT UNIQUE NOT NULL,
    from_address TEXT,
    to_did TEXT,
    usdc_amount DECIMAL(20,6),
    credits_granted INTEGER,
    block_number BIGINT,
    claimed_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS endorsements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endorser_did TEXT NOT NULL,
    endorsed_did TEXT NOT NULL,
    skill TEXT,
    evidence_hash TEXT,
    vc_jwt TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS music_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_did TEXT NOT NULL,
    track_title TEXT,
    track_hash TEXT,
    credential_type TEXT DEFAULT 'MusicProvenanceCredential',
    raw_vc TEXT,
    anchor_tx TEXT,
    anchor_block TEXT,
    revoked BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT NOW()
);
