-- 012_drop_aae_eval_fk.sql
-- D3 Komponente 2 — Korrektur: FK aae_evaluations.aae_ref -> aae_envelopes droppen.
--
-- GRUND: aae_envelopes ist immutable (Migration 010: REVOKE UPDATE,DELETE FROM moltstack
-- als Defense-in-Depth neben dem Immutability-Trigger). Ein FK-Child-INSERT nimmt auf
-- den Parent einen `FOR KEY SHARE`-Lock — der braucht UPDATE/DELETE-Privileg auf den
-- Parent. Da moltstack das auf aae_envelopes NICHT mehr hat (arDxt, kein w/d), schlug
-- JEDER eval-INSERT mit "permission denied for table aae_envelopes" (FK-Lock) fehl.
--
-- FIX (Option B, brief-Fallback "sonst sha256-CHECK"): FK droppen, sha256-Format-CHECK
-- bleibt erzwungen. Konsistent mit violation_records (FK-frei, immutable Audit-Tabelle).
-- Vorteil: evals gegen forged/unbekannte aae_ref werden GELOGGT (Audit-Signal), statt
-- FK-rejected. Store-REVOKE bleibt unangetastet (Immutability via Trigger garantiert).
--
-- Eigenschaften: idempotent (DROP CONSTRAINT IF EXISTS + guarded ADD CHECK), korrektiv
--                (ALTER auf der frisch angelegten aae_evaluations — beabsichtigt),
--                reversibel (DOWN-Block; WARNUNG: FK-Re-Add reaktiviert den Blocker).

-- FK entfernen (idempotent):
ALTER TABLE aae_evaluations DROP CONSTRAINT IF EXISTS aae_evaluations_aae_ref_fkey;

-- sha256-Format-CHECK sicherstellen (idempotent via Guard — falls FK ihn je ersetzt hätte):
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'aae_evaluations_aae_ref_check'
      AND conrelid = 'aae_evaluations'::regclass
  ) THEN
    ALTER TABLE aae_evaluations
      ADD CONSTRAINT aae_evaluations_aae_ref_check
      CHECK (aae_ref ~ '^sha256:[a-f0-9]{64}$');
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- DOWN (manueller Rollback, NICHT von CI ausgefuehrt):
--   ALTER TABLE aae_evaluations
--     ADD CONSTRAINT aae_evaluations_aae_ref_fkey
--     FOREIGN KEY (aae_ref) REFERENCES aae_envelopes(aae_ref);
--   -- WARNUNG: reaktiviert den FK-Lock-vs-REVOKE-Blocker (eval-INSERT bricht wieder).
--   --          Nur sinnvoll mit GRANT UPDATE,DELETE ON aae_envelopes ODER trigger-only-Immutability.
-- ---------------------------------------------------------------------------
