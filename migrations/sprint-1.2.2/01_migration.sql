-- =====================================================================
-- Sprint 1.2.2 — Daten-Bereinigung (v3, hardened post Review 2)
-- =====================================================================

BEGIN;

DO $$
DECLARE
  v_endo_28a    INT;
  v_endo_te5    INT;
  v_endo_662a   INT;
  v_renamed     INT;
  v_revoked_28a INT;
  v_revoked_te5 INT;
  v_seeds_del   INT;
  v_revoked_vco INT;
  v_cache_tbl   BOOLEAN;
  v_cache_del   INT;
BEGIN

UPDATE agents SET display_name = 'TrustScout'
 WHERE did = 'did:moltrust:d34ed796a4dc4698' AND display_name = 'moltguard_v1';
GET DIAGNOSTICS v_renamed = ROW_COUNT;
RAISE NOTICE 'A) display_name renamed (rows: %)', v_renamed;

SELECT COUNT(*) INTO v_endo_28a FROM endorsements
 WHERE endorser_did = 'did:moltrust:28a0984ab85d4c40'
    OR endorsed_did = 'did:moltrust:28a0984ab85d4c40';
IF v_endo_28a > 0 THEN
  RAISE EXCEPTION 'Sprint 1.2.2 B aborted - 28a0984ab85d4c40 has % endorsements', v_endo_28a;
END IF;
UPDATE agents SET revoked_at = NOW(),
       revocation_reason = 'sprint-1.2.2: unused duplicate of d34ed796 (TrustScout)'
 WHERE did = 'did:moltrust:28a0984ab85d4c40' AND revoked_at IS NULL;
GET DIAGNOSTICS v_revoked_28a = ROW_COUNT;
RAISE NOTICE 'B) revoked 28a0984ab85d4c40 (rows: %)', v_revoked_28a;

SELECT COUNT(*) INTO v_endo_te5 FROM endorsements
 WHERE endorser_did = 'did:moltrust:te5tharne550001'
    OR endorsed_did = 'did:moltrust:te5tharne550001';
IF v_endo_te5 > 0 THEN
  RAISE EXCEPTION 'Sprint 1.2.2 C aborted - te5tharne550001 has % endorsements', v_endo_te5;
END IF;
UPDATE agents SET revoked_at = NOW(),
       revocation_reason = 'sprint-1.2.2: test artifact, never used in production'
 WHERE did = 'did:moltrust:te5tharne550001' AND revoked_at IS NULL;
GET DIAGNOSTICS v_revoked_te5 = ROW_COUNT;
RAISE NOTICE 'C) revoked te5tharne550001 (rows: %)', v_revoked_te5;

DELETE FROM swarm_seeds WHERE did = 'did:moltrust:vcone';
GET DIAGNOSTICS v_seeds_del = ROW_COUNT;
RAISE NOTICE 'D) deleted vcone from swarm_seeds (rows: %)', v_seeds_del;
UPDATE agents SET revoked_at = NOW(),
       revocation_reason = 'VCOne deprecated 10.05.2026 (GitHub-Account gelöscht)'
 WHERE did = 'did:moltrust:vcone' AND revoked_at IS NULL;
GET DIAGNOSTICS v_revoked_vco = ROW_COUNT;
RAISE NOTICE 'D) revoked vcone (rows: %)', v_revoked_vco;

SELECT COUNT(*) INTO v_endo_662a FROM endorsements
 WHERE endorser_did = 'did:moltrust:662a7181e0154998'
    OR endorsed_did = 'did:moltrust:662a7181e0154998';
IF v_endo_662a > 0 THEN
  RAISE EXCEPTION 'Sprint 1.2.2 E aborted - 662a7181 has % endorsements', v_endo_662a;
END IF;
DELETE FROM swarm_seeds WHERE did = 'did:moltrust:662a7181e0154998';
GET DIAGNOSTICS v_seeds_del = ROW_COUNT;
RAISE NOTICE 'E) deleted 662a7181 (rows: %)', v_seeds_del;

SELECT EXISTS(SELECT 1 FROM information_schema.tables
               WHERE table_name = 'trust_score_cache') INTO v_cache_tbl;
IF v_cache_tbl THEN
  DELETE FROM trust_score_cache WHERE did IN (
    'did:moltrust:d34ed796a4dc4698','did:moltrust:ambassador0001',
    'did:moltrust:vcone','did:moltrust:662a7181e0154998',
    'did:moltrust:28a0984ab85d4c40','did:moltrust:te5tharne550001');
  GET DIAGNOSTICS v_cache_del = ROW_COUNT;
  RAISE NOTICE 'F) cache invalidated (rows: %)', v_cache_del;
ELSE
  RAISE NOTICE 'F) no trust_score_cache table - skipped';
END IF;

END $$;
COMMIT;
