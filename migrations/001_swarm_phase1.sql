-- MolTrust Swarm Intelligence Protocol Phase 1
-- Nur auf moltstack_sandbox ausführen bis Produktions-Deploy freigegeben

CREATE TABLE IF NOT EXISTS endorsements (
  id                  SERIAL PRIMARY KEY,
  endorser_did        TEXT NOT NULL,
  endorsed_did        TEXT NOT NULL,
  skill               TEXT NOT NULL,
  evidence_hash       TEXT NOT NULL,
  evidence_timestamp  TIMESTAMPTZ NOT NULL,
  base_tx_hash        TEXT,
  vertical            TEXT NOT NULL CHECK (vertical IN (
                        'skill','shopping','travel',
                        'prediction','salesguard','sports','core'
                      )),
  weight              REAL NOT NULL DEFAULT 1.0,
  issued_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at          TIMESTAMPTZ NOT NULL,
  vc_jwt              TEXT,
  UNIQUE(endorser_did, endorsed_did, evidence_hash)
);

CREATE INDEX IF NOT EXISTS idx_endorsements_endorsed_did
  ON endorsements(endorsed_did);
CREATE INDEX IF NOT EXISTS idx_endorsements_endorser_did
  ON endorsements(endorser_did);
CREATE INDEX IF NOT EXISTS idx_endorsements_expires_at
  ON endorsements(expires_at);

CREATE TABLE IF NOT EXISTS trust_score_cache (
  did               TEXT PRIMARY KEY,
  score             REAL NOT NULL,
  endorser_count    INTEGER NOT NULL,
  sybil_penalty     REAL NOT NULL DEFAULT 0.0,
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  cache_valid_until TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trust_score_cache_valid
  ON trust_score_cache(cache_valid_until);
