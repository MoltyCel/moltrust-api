-- Daily usage rollup — survives the request_log prune.
--
-- request_log is owned by the postgres role and keeps 30 days
-- (agents/retention_cleanup.py). Every "usage over the last N months" question
-- therefore had no source: nginx keeps 14 days, the pg_dump backups keep 7, and
-- nothing aggregated the rows before they were deleted.
--
-- These tables are owned by moltstack, are written BEFORE the prune runs, and
-- keep 24 months. They are append-and-upsert only; no row here is ever derived
-- from another row here.

CREATE TABLE IF NOT EXISTS usage_daily (
    day           date        NOT NULL,
    endpoint_key  text        NOT NULL,
    status_code   integer     NOT NULL,
    source        text        NOT NULL,
    traffic_class text        NOT NULL,
    requests      bigint      NOT NULL,
    distinct_ips  integer     NOT NULL,
    distinct_dids integer     NOT NULL,
    rolled_up_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, endpoint_key, status_code, source, traffic_class)
);

CREATE INDEX IF NOT EXISTS idx_usage_daily_day      ON usage_daily (day DESC);
CREATE INDEX IF NOT EXISTS idx_usage_daily_endpoint ON usage_daily (endpoint_key, day DESC);
CREATE INDEX IF NOT EXISTS idx_usage_daily_class    ON usage_daily (traffic_class, day DESC);

-- Settlement counters request_log cannot answer on its own. A 402 and a later
-- 200 are two independent rows there with nothing linking them; only
-- payment_events records that money actually moved.
CREATE TABLE IF NOT EXISTS usage_daily_payments (
    day              date        NOT NULL PRIMARY KEY,
    challenges_402   bigint      NOT NULL,
    settled_payments bigint      NOT NULL,
    distinct_wallets integer     NOT NULL,
    usdc_total       numeric(18,6) NOT NULL,
    rolled_up_at     timestamptz NOT NULL DEFAULT now()
);

-- request_log carries no API-key column and cannot grow one: it is owned by the
-- postgres role and the application role may not ALTER it. Distinct-key counts
-- are therefore recorded at request time, keyed by an irreversible fingerprint
-- rather than the key itself.
CREATE TABLE IF NOT EXISTS usage_daily_keys (
    day    date   NOT NULL,
    key_fp text   NOT NULL,
    did    text,
    calls  bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key_fp)
);

CREATE INDEX IF NOT EXISTS idx_usage_daily_keys_day ON usage_daily_keys (day DESC);
