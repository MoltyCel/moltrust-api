-- Durable samples of the MolTrust gate counters.
--
-- moltguard keeps its discount counters in process memory and says so
-- (`since_process_start: true` in /guard/moltrust/gate-stats). That is the
-- right choice there — the request path stays free of a database write — but
-- it means a deploy or a crash erases the measurement, and the question
-- "did verification move any money since 2026-09-21 21:02 UTC" cannot be
-- answered from a counter that resets.
--
-- scripts/gate_measure.py samples the endpoint and stores the increase since
-- the previous sample. Summing `priced_delta` over any window gives a figure
-- that survives restarts.
--
-- Role-owned side table: the app role has no DDL on the postgres-owned tables
-- (see CLAUDE.md), so this lives beside them rather than inside them.

CREATE TABLE IF NOT EXISTS gate_measurement (
    id                bigserial PRIMARY KEY,
    measured_at       timestamptz NOT NULL DEFAULT now(),
    -- The counters exactly as the process reported them, kept so a sample can
    -- be re-derived if the delta logic is ever found wrong.
    priced_raw        bigint      NOT NULL,
    discounted_raw    bigint      NOT NULL,
    -- The increase since the previous sample. This is what reports sum.
    priced_delta      bigint      NOT NULL,
    discounted_delta  bigint      NOT NULL,
    denied_by_reason  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- True when the raw counter came back lower than the previous sample,
    -- which means the process restarted in between. The delta is then the
    -- whole current value, and anything the old process served after the last
    -- sample is lost. Sampling every 15 minutes keeps that window small; the
    -- flag marks the samples where the figure is a floor rather than a count.
    restarted         boolean     NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS gate_measurement_measured_at_idx
    ON gate_measurement (measured_at DESC);
