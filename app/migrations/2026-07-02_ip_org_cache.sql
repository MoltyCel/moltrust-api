-- ip_org_cache — lazy IPinfo Lite org (as_name) lookups for the admin callers
-- dashboard. Keyed by /24 prefix, 24h TTL. Populated on dashboard READ only
-- (never on the request hot path). Mirrors app/geo.ensure_table(). 2026-07-02.
CREATE TABLE IF NOT EXISTS ip_org_cache (
  ip_prefix  varchar(45) PRIMARY KEY,
  as_name    varchar(200),
  country    varchar(100),
  updated_at timestamptz DEFAULT now()
);
