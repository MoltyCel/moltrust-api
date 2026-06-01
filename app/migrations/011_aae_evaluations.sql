-- 011_aae_evaluations.sql
-- D3 MANDATE-Enforcement, Komponente 2: AAE Evaluator — Eval-Store + signierter Audit-Trail.
-- Schema exakt aus Evaluator-Brief v4 (FINAL, PR #115).
-- Eigenschaften: idempotent (IF NOT EXISTS / OR REPLACE / DROP-before-CREATE),
--                additiv (nur CREATE/REVOKE, kein ALTER an Bestandstabellen),
--                reversibel (DOWN-Block am Ende, manuell).
--
-- Zweck: (1) Count-Quelle fuer rate_limit/single_use (Count ueber DIESE Tabelle,
--        NICHT IPR — IPR wird erst nach der Aktion geloggt); (2) Ed25519-signierter
--        Audit-Trail jeder Evaluation. APPEND-ONLY (immutable, wie aae_envelopes).

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;  -- bereits vorhanden -> no-op

CREATE TABLE IF NOT EXISTS aae_evaluations (
    eval_id            text PRIMARY KEY
                       DEFAULT ('eval_' || encode(gen_random_bytes(16), 'hex')),  -- app darf eigene eval_id liefern
    -- aae_ref referenziert einen EXISTIERENDEN (immutable) Envelope; not-found ist
    -- ein Fast-Path-DENY OHNE eval-Row, daher ist der FK hier immer erfuellbar.
    aae_ref            varchar(100) NOT NULL
                       REFERENCES aae_envelopes(aae_ref)
                       CHECK (aae_ref ~ '^sha256:[a-f0-9]{64}$'),   -- belt-and-suspenders, wie Store
    agent_did          varchar(255) NOT NULL,
    action_context     jsonb        NOT NULL CHECK (jsonb_typeof(action_context) = 'object'),
    evaluations        jsonb        NOT NULL CHECK (jsonb_typeof(evaluations) = 'array'),  -- per-constraint verdicts
    verdict            text         NOT NULL CHECK (verdict IN ('ALLOW', 'DENY')),
    value_source       text         NOT NULL CHECK (value_source IN ('rail_verified', 'self_asserted', 'n/a')),
    evaluator_version  text         NOT NULL,
    nonce              text         NOT NULL,
    verdict_signature  text         NOT NULL,   -- Ed25519(DOMAIN_TAG_BYTES || JCS(full canonical record))
    verdict_kid        text         NOT NULL,   -- Key-ID des registry-keys (Rotation)
    created_at         timestamptz  NOT NULL DEFAULT now(),   -- server-set, NICHT client
    -- Replay-Schutz: derselbe (agent, nonce) kann nicht zweimal eingespielt werden.
    CONSTRAINT uq_aae_eval_nonce UNIQUE (agent_did, nonce)
);

-- rate_limit-Count-Pfad: COUNT WHERE agent_did=$ AND aae_ref=$ AND verdict='ALLOW' AND created_at >= now()-window
CREATE INDEX IF NOT EXISTS idx_aae_eval_ratelimit
    ON aae_evaluations (agent_did, aae_ref, created_at);

-- Immutability: append-only Audit-Trail; UPDATE/DELETE hart verbieten.
CREATE OR REPLACE FUNCTION aae_evaluations_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'aae_evaluations is append-only: % verboten', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_aae_eval_immutable ON aae_evaluations;  -- Postgres kennt kein CREATE TRIGGER IF NOT EXISTS
CREATE TRIGGER trg_aae_eval_immutable BEFORE UPDATE OR DELETE ON aae_evaluations
  FOR EACH ROW EXECUTE FUNCTION aae_evaluations_immutable();

-- Grant-level Defense-in-Depth (zweite Schranke neben dem Trigger). Idempotent.
REVOKE UPDATE, DELETE ON aae_evaluations FROM moltstack;

-- ---------------------------------------------------------------------------
-- DOWN (manueller Rollback, NICHT von CI ausgefuehrt):
--   DROP TABLE IF EXISTS aae_evaluations CASCADE;     -- entfernt Tabelle + Index + Trigger
--   DROP FUNCTION IF EXISTS aae_evaluations_immutable();
--   -- pgcrypto bleibt (andere Komponenten) -> NICHT droppen.
-- ---------------------------------------------------------------------------
