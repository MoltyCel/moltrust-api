-- 013_issuer_trust_tier.sql
-- D-1 Acceptance-Gate: issuer-Trust-Tier auf aae_envelopes (additive).
-- trusted = Issuer verifiziert ueber bekannte Quelle (did:moltrust Registry);
-- unverified_issuer = valide Signatur, aber unbekannter Issuer (Phase B did:web).
-- Mitgefuehrt analog Evaluator-value_source; nachgelagerte Schichten koennen bei
-- unverified_issuer strenger sein. ADD COLUMN auf immutable Tabelle ist DDL (kein
-- Row-UPDATE) -> Immutability-Trigger/REVOKE unberuehrt. Idempotent, additiv, reversibel.

ALTER TABLE aae_envelopes ADD COLUMN IF NOT EXISTS issuer_trust_tier text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'aae_envelopes_issuer_trust_tier_check'
      AND conrelid = 'aae_envelopes'::regclass
  ) THEN
    ALTER TABLE aae_envelopes
      ADD CONSTRAINT aae_envelopes_issuer_trust_tier_check
      CHECK (issuer_trust_tier IS NULL OR issuer_trust_tier IN ('trusted', 'unverified_issuer'));
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- DOWN (manuell, NICHT von CI):
--   ALTER TABLE aae_envelopes DROP CONSTRAINT IF EXISTS aae_envelopes_issuer_trust_tier_check;
--   ALTER TABLE aae_envelopes DROP COLUMN IF EXISTS issuer_trust_tier;
-- ---------------------------------------------------------------------------
