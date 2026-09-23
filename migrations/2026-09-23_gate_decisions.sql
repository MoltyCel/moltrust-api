-- 2026-09-23_gate_decisions.sql
-- One row per gate decision, so a discount can be attributed to a DID.
--
-- `gateStats` counts in process memory and `gate_measurement` keeps the sums.
-- Both answer "how many"; neither answers "which agent". That gap is what made
-- the round-2 sighting check the anchor instead of the gate: an attestation
-- cannot carry track_record without an anchored credential, so the anchor is a
-- sound proxy — but it proves an agent *could* have earned the discount, not
-- that it did.
--
-- Role-owned side table. The app role has DML and no DDL on the postgres-owned
-- tables; same constraint as agent_profile and credential_anchors, same answer.
--
-- Apply by hand, before deploying the code that writes it:
--   psql -h localhost -U moltstack -d moltstack \
--        -f migrations/2026-09-23_gate_decisions.sql

BEGIN;

CREATE TABLE IF NOT EXISTS gate_decisions (
    id      bigserial   PRIMARY KEY,
    ts      timestamptz NOT NULL DEFAULT now(),
    -- Null on the denials that happen before an attestation is read:
    -- attestation_missing, proof_missing, attestation_invalid. Those requests
    -- have no identity to attribute, and writing a placeholder would invent one.
    did     text,
    path    text        NOT NULL,
    -- What the caller was quoted, in USDC base units. The discounted figure on
    -- an allow, the list price on a denial — so the row carries the difference
    -- the decision made, not just its name.
    amount  integer,
    -- 'ok' on an allow, otherwise the DenialReason the gate returned. An allow
    -- that came through the track record reads 'ok' with via='track_record'.
    reason  text        NOT NULL,
    -- Which requirement carried an allow: 'score' or 'track_record'. Null on a
    -- denial. Kept out of `reason` because they answer different questions and
    -- a combined string would have to be parsed to ask either one.
    via     text
);

-- The two questions asked of this table: what happened lately, and what did
-- this DID do. Retention prunes by ts, which the first index already serves.
CREATE INDEX IF NOT EXISTS idx_gate_decisions_ts  ON gate_decisions (ts DESC);
CREATE INDEX IF NOT EXISTS idx_gate_decisions_did ON gate_decisions (did) WHERE did IS NOT NULL;

COMMIT;

-- Retention: 90 days, pruned by the writer rather than by cron.
--
-- A cron entry is server infrastructure and is not repo-managed, so it drifts
-- out of sight; a timer inside the process dies with the process. The writer
-- deletes expired rows at most once an hour, which costs one indexed delete per
-- hour and cannot be forgotten, because the thing that fills the table is the
-- thing that empties it.
--
--   DELETE FROM gate_decisions WHERE ts < now() - interval '90 days';
