-- ip_geo_cache — per-/24 geo enrichment cache (FIX 3, 2026-06-11).
-- Decouples ip-api.com lookups from the request hot path; FastAPI + MoltGuard
-- both read it, a background warmer populates it on cache miss.
CREATE TABLE IF NOT EXISTS ip_geo_cache (
  ip_prefix   varchar(45) PRIMARY KEY,
  ip_org      varchar(200),
  ip_country  varchar(100),
  status      varchar(16) DEFAULT 'ok',
  enriched_at timestamptz DEFAULT now()
);
