-- =====================================================================
-- Sprint 1.2.2 — Rollback v3
-- vcone und display_name 'TrustScout' bewusst nicht rolled back.
-- =====================================================================

BEGIN;

DO $$
DECLARE
  v_undone_28a    INT;
  v_undone_te5    INT;
  v_inserted_662a INT;
  v_already_662a  BOOLEAN;
  v_cache_tbl     BOOLEAN;
  v_cache_del     INT;
BEGIN

RAISE NOTICE 'A) display_name rename intentionally not reverted (cosmetic).';

UPDATE agents SET revoked_at = NULL, revocation_reason = NULL
 WHERE did = 'did:moltrust:28a0984ab85d4c40'
   AND revocation_reason LIKE 'sprint-1.2.2:%';
GET DIAGNOSTICS v_undone_28a = ROW_COUNT;
RAISE NOTICE 'B) un-revoked 28a0984ab85d4c40 (rows: %)', v_undone_28a;

UPDATE agents SET revoked_at = NULL, revocation_reason = NULL
 WHERE did = 'did:moltrust:te5tharne550001'
   AND revocation_reason LIKE 'sprint-1.2.2:%';
GET DIAGNOSTICS v_undone_te5 = ROW_COUNT;
RAISE NOTICE 'C) un-revoked te5tharne550001 (rows: %)', v_undone_te5;

RAISE NOTICE 'D) vcone intentionally excluded from rollback.';

-- E) 662a7181 restore. Werte verifiziert gegen live swarm_seeds 2026-05-22:
--    label='Seeded agent', base_score=70, registered_at=2026-04-04 08:24:20.917883+00
SELECT EXISTS(SELECT 1 FROM swarm_seeds
               WHERE did = 'did:moltrust:662a7181e0154998') INTO v_already_662a;
IF v_already_662a THEN
  RAISE NOTICE 'E) 662a7181 already present - INSERT skipped';
ELSE
  INSERT INTO swarm_seeds (did, label, base_score, registered_at)
  VALUES ('did:moltrust:662a7181e0154998', 'Seeded agent', 70.0,
          '2026-04-04 08:24:20.917883+00')
  ON CONFLICT (did) DO NOTHING;
  GET DIAGNOSTICS v_inserted_662a = ROW_COUNT;
  RAISE NOTICE 'E) restored 662a7181 (rows: %)', v_inserted_662a;
END IF;

SELECT EXISTS(SELECT 1 FROM information_schema.tables
               WHERE table_name = 'trust_score_cache') INTO v_cache_tbl;
IF v_cache_tbl THEN
  DELETE FROM trust_score_cache WHERE did IN (
    'did:moltrust:d34ed796a4dc4698','did:moltrust:ambassador0001',
    'did:moltrust:662a7181e0154998','did:moltrust:28a0984ab85d4c40',
    'did:moltrust:te5tharne550001');
  GET DIAGNOSTICS v_cache_del = ROW_COUNT;
  RAISE NOTICE 'F) cache invalidated (rows: %)', v_cache_del;
ELSE
  RAISE NOTICE 'F) no trust_score_cache table - skipped';
END IF;

END $$;
COMMIT;
