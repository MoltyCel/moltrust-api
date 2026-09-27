-- registry-proof export. Read-only. Produces one JSON object per line.
--
-- Snapshot boundary: anchors and registrations from the cutoff onwards are out,
-- so the file is stable while new agents keep arriving.
--
-- The platform universe is an allow-list, not a deny-list. A platform that is
-- not named in one of the three bucket lists below produces no row at all, and
-- the counted total is exactly the sum of the three.
-- AS_OF is supplied by the caller (-v AS_OF=...). No default: a snapshot that
-- silently falls back to a date in the file is a snapshot nobody chose.

WITH
bucket_map(platform, bucket) AS (VALUES
  -- paid bounty rounds, itemised row by row
  ('taskmarket','bounty'), ('a2a','bounty'),
  -- partners, aggregated into a single row downstream
  ('ownify','partner'), ('klaw','partner'),
  -- everything that registered on its own
  ('moltbook','organic'), ('base','organic'), ('agentnexus','organic'),
  ('github','organic'), ('kubernetes','organic'), ('openclaw','organic'),
  ('openclaw-k8s','organic'), ('clawhub','organic'), ('generic','organic'),
  ('custom','organic'), ('solana','organic'),
  -- ours; carried in the file, never counted in the headline
  ('test','own_test'), ('system','own_test'), ('moltrust','own_test'), ('gate','own_test')
),
anch AS (
  SELECT c.subject_did AS did, c.id AS cred_id, c.credential_type, c.issued_at, c.proof_value,
         k.tx_hash, k.block, k.merkle_root, k.merkle_proof, k.anchored_at
    FROM credentials c
    JOIN credential_anchors k ON k.credential_id = c.id
   WHERE k.anchored_at < :'AS_OF'
),
act AS (
  SELECT r.agent_did AS did,
         bool_or(NOT (r.endpoint LIKE ANY (ARRAY[
           '/identity/verify/%','/skill/trust-score/%','/identity/erc8004/register%',
           '/identity/bind%','/identity/nonce%','/credentials/track-record%']))) AS activated,
         min(CASE WHEN NOT (r.endpoint LIKE ANY (ARRAY[
           '/identity/verify/%','/skill/trust-score/%','/identity/erc8004/register%',
           '/identity/bind%','/identity/nonce%','/credentials/track-record%']))
             THEN regexp_replace(r.endpoint,'^(/[^/]+(/[^/]+)?).*','\1') END)   AS act_prefix,
         max(r.ts) AS last_call
    FROM request_log r
   WHERE r.agent_did IS NOT NULL
     AND r.ts < :'AS_OF'   -- the activation window ends at the snapshot too, or the figure drifts
   GROUP BY r.agent_did
)
SELECT row_to_json(t) FROM (
  SELECT a.did,
         m.bucket,
         a.created_at::date::text                          AS registered,
         (a.created_at < '2026-09-14'::date)               AS before_telemetry_cutoff,
         coalesce(act.activated, false)                    AS activated,
         act.act_prefix                                    AS activated_endpoint_prefix,
         act.last_call::date::text                         AS last_independent_call_day,
         (SELECT json_agg(json_build_object(
                    'credential_id', x.cred_id, 'credential_type', x.credential_type,
                    'issued_at', to_char(x.issued_at,'YYYY-MM-DD"T"HH24:MI:SS.US'),
                    'merkle_proof', x.merkle_proof,
                    'anchor_tx', x.tx_hash, 'anchor_block', x.block)
                  ORDER BY x.cred_id)
            FROM anch x WHERE x.did = a.did)               AS anchors
    FROM agents a
    JOIN bucket_map m ON m.platform = coalesce(a.platform,'')
    LEFT JOIN act ON act.did = a.did
   WHERE a.revoked_at IS NULL
     AND a.created_at < :'AS_OF'
     AND EXISTS (SELECT 1 FROM anch x WHERE x.did = a.did)
   ORDER BY a.created_at, a.did
) t;
