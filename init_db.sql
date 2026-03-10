CREATE TABLE agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  did TEXT UNIQUE NOT NULL,
  public_key TEXT NOT NULL,
  display_name TEXT,
  platform TEXT DEFAULT 'moltbook',
  created_at TIMESTAMP DEFAULT NOW(),
  reputation_score DECIMAL(5,2) DEFAULT 0.00
);

CREATE TABLE ratings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  from_agent UUID REFERENCES agents(id),
  to_agent UUID REFERENCES agents(id),
  score INTEGER CHECK (score BETWEEN 1 AND 5),
  context TEXT,
  transaction_hash TEXT,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE skills (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  developer_agent UUID REFERENCES agents(id),
  security_score DECIMAL(3,2),
  price_usdc DECIMAL(10,2),
  price_sats BIGINT,
  repo_url TEXT,
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  buyer_agent UUID REFERENCES agents(id),
  skill_id UUID REFERENCES skills(id),
  amount DECIMAL(10,2),
  currency TEXT CHECK (currency IN ('USDC', 'BTC')),
  payment_hash TEXT,
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMP DEFAULT NOW()
);
