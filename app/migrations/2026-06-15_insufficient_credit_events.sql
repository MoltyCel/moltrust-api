-- insufficient_credit_events — records caller_did on every insufficient-credits
-- 402 (credit middleware), so /admin/dashboard/overview can attribute
-- rate-limiting to agents (request_log.agent_did is null on these). 2026-06-15.
CREATE TABLE IF NOT EXISTS insufficient_credit_events (
  id bigserial PRIMARY KEY,
  did text NOT NULL,
  cost bigint,
  balance bigint,
  endpoint text,
  ts timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ice_ts ON insufficient_credit_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_ice_did ON insufficient_credit_events (did, ts DESC);
