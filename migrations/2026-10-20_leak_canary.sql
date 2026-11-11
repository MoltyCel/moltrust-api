-- Leak canary for delegating agents (ops LC-1, docs/leak-canary.md).
--
-- The credential itself lives in `credentials`, like every credential. These
-- tables hold what only the issuer may see: which counterparty received which
-- canary, what was observed, and which counterparties are on hold. No personal
-- data: DIDs, digests and timestamps only.
--
-- Apply by hand: psql -h localhost -U moltstack -d moltstack -f <this file>

CREATE TABLE IF NOT EXISTS leak_canaries (
    id               uuid PRIMARY KEY,
    issuer_did       varchar NOT NULL,
    counterparty_did varchar NOT NULL,
    mode             varchar NOT NULL CHECK (mode IN ('bound', 'decoy')),
    on_leak          varchar NOT NULL CHECK (on_leak IN ('hold', 'none')),
    credential_type  varchar NOT NULL,
    proof_value      text NOT NULL UNIQUE,
    canary_ref       varchar NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS leak_canaries_issuer_idx ON leak_canaries (issuer_did, created_at DESC);

CREATE TABLE IF NOT EXISTS leak_canary_events (
    id               uuid PRIMARY KEY,
    canary_id        uuid NOT NULL REFERENCES leak_canaries (id),
    issuer_did       varchar NOT NULL,
    counterparty_did varchar NOT NULL,
    presenter_did    varchar,
    observed_at      timestamptz NOT NULL,
    event            jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS leak_canary_events_issuer_idx ON leak_canary_events (issuer_did, observed_at DESC);
CREATE INDEX IF NOT EXISTS leak_canary_events_canary_idx ON leak_canary_events (canary_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS leak_canary_holds (
    issuer_did       varchar NOT NULL,
    counterparty_did varchar NOT NULL,
    disposition      varchar NOT NULL CHECK (disposition = 'hold'),
    event_id         uuid NOT NULL REFERENCES leak_canary_events (id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    released_at      timestamptz,
    PRIMARY KEY (issuer_did, counterparty_did)
);

COMMENT ON TABLE leak_canaries IS
  'Canary credential per issuer and counterparty (ops LC-1). The only place that marks a credential as a canary.';
COMMENT ON TABLE leak_canary_events IS
  'Signed leak events, one per canary and presenter per ten minutes (ops LC-1).';
COMMENT ON TABLE leak_canary_holds IS
  'Counterparties an issuer holds after a leak event; read by POST /canary/enforce-hook (ops LC-1).';
