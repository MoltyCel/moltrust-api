-- 010_aae_envelopes.sql
-- D3 MANDATE-Enforcement, Komponente 1: aae_envelopes Store.
-- Erster echter Enforcement-Code nach ADR-D3-v3 (ACCEPTED) + Brief #108.
-- Eigenschaften: idempotent (IF NOT EXISTS / OR REPLACE / DROP-before-CREATE),
--                additiv (nur CREATE/REVOKE, kein ALTER an Bestandstabellen),
--                reversibel (siehe DOWN-Block am Ende, manuell).
--
-- aae_ref  = SHA-256-Content-Hash (PK), byte-identisch zu interaction_proof_records.aae_ref.
-- aae_id   = VC-Identifier (Join an agent_delegations); varchar(255) = KNOWN LIMIT (von live geerbt).
-- issuer_did / envelope_signature = NUR Storage; Signatur-VERIFY = Acceptance-Gate (D-1), nicht hier.

-- pgcrypto in public pinnen (gegen search-path-hijack):
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

CREATE TABLE IF NOT EXISTS aae_envelopes (
    aae_ref            varchar(100) PRIMARY KEY
                       CHECK (aae_ref ~ '^sha256:[a-f0-9]{64}$'),
    aae_id             varchar(255) NOT NULL,                                  -- KNOWN LIMIT 255 (live agent_delegations.aae_id)
    issuer_did         varchar(255) NOT NULL,                                  -- Origin-Storage; VERIFY = Acceptance-Gate D-1
    envelope_signature text         NOT NULL,                                  -- AAE-Signatur-Storage; VERIFY deferred D-1
    mandate_scope      jsonb        NOT NULL,
    constraints        jsonb        NOT NULL CHECK (jsonb_typeof(constraints) = 'array'),  -- required INLINE pro Objekt (C5)
    validity           jsonb        NOT NULL CHECK (jsonb_typeof(validity) = 'object'),
    scope_canonical    bytea        NOT NULL CHECK (octet_length(scope_canonical) <= 8192),   -- JCS-canonical, app-seitig vor INSERT
    aae_version        varchar(20)  NOT NULL,                                  -- Version-Pinning
    taxonomy_version   varchar(20)  NOT NULL,
    evaluator_version  varchar(20),                                           -- NULLable (Pin erst beim ersten Enforcement)
    raw_canonical      bytea        NOT NULL CHECK (octet_length(raw_canonical) <= 1048576),  -- Re-Verify-Quelle (Hash-Recompute)
    created_at         timestamptz  NOT NULL DEFAULT now()
);

-- Hash-Binding als DB-Invariante: aae_ref wird server-seitig aus raw_canonical berechnet (kein App-Vertrauen).
CREATE OR REPLACE FUNCTION aae_envelopes_bind_ref() RETURNS trigger AS $$
BEGIN
  NEW.aae_ref := 'sha256:' || encode(digest(NEW.raw_canonical, 'sha256'), 'hex');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
-- Postgres kennt kein CREATE TRIGGER IF NOT EXISTS -> DROP-before-CREATE fuer echte Idempotenz:
DROP TRIGGER IF EXISTS trg_aae_bind_ref ON aae_envelopes;
CREATE TRIGGER trg_aae_bind_ref BEFORE INSERT ON aae_envelopes
  FOR EACH ROW EXECUTE FUNCTION aae_envelopes_bind_ref();

-- Immutability: Envelopes sind append-only; UPDATE/DELETE hart verbieten.
CREATE OR REPLACE FUNCTION aae_envelopes_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'aae_envelopes is append-only: % verboten', TG_OP;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_aae_immutable ON aae_envelopes;
CREATE TRIGGER trg_aae_immutable BEFORE UPDATE OR DELETE ON aae_envelopes
  FOR EACH ROW EXECUTE FUNCTION aae_envelopes_immutable();

-- Grant-level Defense-in-Depth (zweite Schranke neben dem Immutability-Trigger). Idempotent.
REVOKE UPDATE, DELETE ON aae_envelopes FROM moltstack;

-- single_use Replay-Schutz auf gehashtem scope (fixed-size 32 Byte -> umgeht B-Tree-~2712-Byte-Limit).
-- JCS sorgt fuer byte-identische Kanonisierung semantisch gleicher Scopes -> gleicher Hash -> Unique-Violation = DENY.
CREATE UNIQUE INDEX IF NOT EXISTS uq_aae_single_use
  ON aae_envelopes (aae_id, digest(scope_canonical, 'sha256'));

-- Join-Beschleunigung an Delegation/Evidence:
CREATE INDEX IF NOT EXISTS idx_aae_envelopes_aae_id ON aae_envelopes (aae_id);

-- ---------------------------------------------------------------------------
-- DOWN (manueller Rollback, NICHT von CI ausgefuehrt):
--   DROP TABLE IF EXISTS aae_envelopes CASCADE;        -- entfernt Tabelle + Indizes + Trigger
--   DROP FUNCTION IF EXISTS aae_envelopes_bind_ref();
--   DROP FUNCTION IF EXISTS aae_envelopes_immutable();
--   -- pgcrypto bleibt (von anderen Komponenten genutzt) -> NICHT droppen.
-- ---------------------------------------------------------------------------
