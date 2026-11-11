-- The standing round (ops G4-3): the referral cases one settlement week may
-- touch. Read-only. One JSON object per line, in the shape
-- scripts/dauerrunde.py `abrechnung` takes.
--
-- This gathers facts and decides nothing. Which call is an activation, which
-- referral counts and who is paid is decided in scripts/dauerrunde.py; the
-- prescribed endpoints are not filtered here, because their list lives in
-- app/sql/registry_export.sql and a second copy would drift.
--
-- A case belongs to a week through its activation, or, without one, through
-- the day its 14-day window closes. Both lie in [VON, BIS) only for agents
-- registered in [VON - 14 days, BIS), so nothing older is read.
--
-- `aufrufe` holds the first call per endpoint, which is all the activation
-- test needs (the earliest call to an endpoint no task text prescribes), and
-- keeps the row small. request_log keeps 30 days; the oldest call this reads
-- is 21 days before the Monday run.
--
-- `netz` carries the raw registration_ip; the script cuts it to the /24.
--
-- Run with: psql -v VON=<iso> -v BIS=<iso> -f app/sql/dauerrunde_faelle.sql
WITH faelle AS (
  SELECT f.did, f.referrer_did,
         (a.created_at AT TIME ZONE 'UTC') AS reg
    FROM agent_referrals f
    JOIN agents a ON a.did = f.did
   WHERE (a.created_at AT TIME ZONE 'UTC') >= :'VON'::timestamptz - interval '14 days'
     AND (a.created_at AT TIME ZONE 'UTC') <  :'BIS'::timestamptz
)
SELECT json_build_object(
  'empfohlen', json_build_object(
      'did', a.did,
      'registriert_am', x.reg,
      'adresse', a.wallet_address,
      'kette', a.wallet_chain,
      'schluessel', a.public_key_hex,
      'betreiber', a.operator_did,
      'netz', a.registration_ip),
  'empfehlend', json_build_object(
      'did', x.referrer_did,
      'adresse', b.wallet_address,
      'kette', b.wallet_chain,
      'schluessel', b.public_key_hex,
      'betreiber', b.operator_did,
      'netz', b.registration_ip),
  'fakt', json_build_object(
      'did', x.did,
      'self', x.did = x.referrer_did,
      'mutual', EXISTS (SELECT 1 FROM agent_referrals m
                         WHERE m.did = x.referrer_did AND m.referrer_did = x.did),
      'unknown', false,
      'revoked', a.revoked_at IS NOT NULL),
  'aufrufe', COALESCE((
      SELECT json_agg(json_build_object('ts', e.ts, 'endpoint', e.endpoint)
                      ORDER BY e.ts)
        FROM (SELECT r.endpoint, min(r.ts) AS ts
                FROM request_log r
               WHERE r.agent_did = x.did
                 AND r.ts >= x.reg
                 AND r.ts <  :'BIS'::timestamptz
               GROUP BY r.endpoint) e), '[]'::json)
)
  FROM faelle x
  JOIN agents a ON a.did = x.did
  LEFT JOIN agents b ON b.did = x.referrer_did
 ORDER BY x.reg, x.did;
