-- The public agent count, and the only place it is defined.
--
-- Decision of 2026-09-26 (docs/decisions/0004-zieldatum-1000.md): the number we
-- say in public is external agents that hold at least one anchored credential,
-- with our own agents and the partners' test agents taken out and shown
-- separately. The stricter figure from CLAUDE.md -- an authenticated call to an
-- endpoint no bounty task named -- is reported alongside it, never instead of it.
--
-- Blog, weekly proof, Telegram, the milestone trigger and registry-proof.json
-- all read this file. Two reports must not answer two questions under one word;
-- that is how "112 DIDs / 99 without a call" and "114 / 100 with a call" came to
-- contradict each other on 2026-09-21.
--
--   psql -h localhost -U moltstack -d moltstack -X -f app/sql/public_count.sql
--
-- The counted, the deducted and the excluded add up to every non-revoked row.
-- The last query is that completeness check and returns t or the file is wrong.

\set ON_ERROR_STOP on

-- Endpoints a bounty task text told the agent to call. This list must stay
-- identical to SCRIPTED_ENDPOINTS in agents/proof_post.py, and it grows with
-- every bounty. Round 2 added the wallet-binding steps.
-- Plain \set, not the triple-quoted form. '''x''' leaves a literal quote on the
-- first and last element after :'scripted' substitution, so /identity/verify/
-- and /credentials/track-record stopped matching and the activated column read
-- 37 bounty agents instead of 21 on 2026-09-26. A filter that is accepted and
-- ignored is the same failure as payTo on the CDP catalogue.
\set scripted /identity/verify/,/skill/trust-score/,/identity/erc8004/register,/identity/bind,/identity/nonce,/auth/signup-did,/credentials/track-record

DROP VIEW IF EXISTS pc_base CASCADE;
CREATE TEMP VIEW pc_base AS
SELECT a.did,
       a.platform,
       a.display_name,
       a.created_at,
       CASE
         WHEN a.agent_type <> 'external'                            THEN 'intern'
         WHEN a.platform IN ('test','system','moltrust','gate')     THEN 'intern'
         WHEN a.platform IN ('ownify','aeoess','klaw')
              AND a.display_name ~* '(test|demo|sample|dummy|probe|staging)'
                                                                    THEN 'partner-test'
         WHEN a.platform IN ('ownify','aeoess','klaw')              THEN 'partner'
         WHEN a.platform IN ('taskmarket','a2a')                    THEN 'bounty'
         ELSE 'organisch'
       END AS bucket,
       EXISTS (SELECT 1
                 FROM credentials c
                 JOIN credential_anchors ca ON ca.credential_id = c.id
                WHERE c.subject_did = a.did AND NOT c.revoked) AS anchored,
       EXISTS (SELECT 1
                 FROM request_log r
                WHERE r.agent_did = a.did
                  AND NOT EXISTS (SELECT 1
                                    FROM unnest(string_to_array(:'scripted', ',')) s
                                   WHERE r.endpoint LIKE '%' || s || '%')) AS off_script,
       -- request_log holds 30 days. An agent that registered before the window
       -- opened and has no row in it is unmeasured, not inactive, and it is
       -- counted in its own column rather than as a zero.
       (a.created_at >= (SELECT min(ts) FROM request_log)
        OR EXISTS (SELECT 1 FROM request_log r WHERE r.agent_did = a.did)) AS measurable
  FROM agents a
 WHERE a.revoked_at IS NULL;

\echo '== public count, by bucket =='
SELECT bucket,
       count(*)                                          AS agents,
       count(*) FILTER (WHERE anchored)                  AS anchored,
       count(*) FILTER (WHERE anchored AND off_script)   AS activated,
       count(*) FILTER (WHERE anchored AND NOT measurable) AS unmeasured
  FROM pc_base
 GROUP BY bucket
 ORDER BY CASE bucket WHEN 'bounty' THEN 1 WHEN 'partner' THEN 2
                      WHEN 'organisch' THEN 3 WHEN 'partner-test' THEN 4
                      ELSE 5 END;

\echo '== the two headline figures =='
SELECT count(*) FILTER (WHERE bucket NOT IN ('intern','partner-test')
                          AND anchored)                       AS public_count,
       count(*) FILTER (WHERE bucket NOT IN ('intern','partner-test')
                          AND anchored AND off_script)        AS activated,
       count(*) FILTER (WHERE bucket NOT IN ('intern','partner-test')
                          AND anchored AND NOT measurable)    AS unmeasured,
       count(*) FILTER (WHERE bucket IN ('intern','partner-test')) AS deducted,
       count(*)                                                AS all_live
  FROM pc_base;

\echo '== registrations per day, last 14 =='
SELECT created_at::date AS day, count(*) AS registered
  FROM pc_base
 WHERE created_at >= current_date - 13
 GROUP BY 1 ORDER BY 1;

-- The trigger reads this one. rate7 over the counted buckets only, because a
-- burst of our own test registrations must not pull a milestone forward.
\echo '== rate7 over the counted buckets =='
SELECT round(count(*) / 7.0, 2) AS rate7
  FROM pc_base
 WHERE bucket NOT IN ('intern','partner-test')
   AND created_at >= now() - interval '7 days';

\echo '== completeness =='
-- Every non-revoked row landed in exactly one bucket; the activation test ran
-- against a log that is actually there; and the scripted list arrived with all
-- seven entries intact, none of them carrying a stray quote. The window start
-- is printed so a reader sees which registrations the activated column covers.
SELECT (SELECT count(*) FROM pc_base)
         = (SELECT count(*) FROM agents WHERE revoked_at IS NULL) AS buckets_cover_all,
       (SELECT count(*) FROM unnest(string_to_array(:'scripted', ','))) = 7
                                                                  AS scripted_intact,
       NOT EXISTS (SELECT 1 FROM unnest(string_to_array(:'scripted', ',')) s
                    WHERE s NOT LIKE '/%')                        AS scripted_unquoted,
       (SELECT min(ts) FROM request_log)                          AS log_window_starts,
       (SELECT count(*) FROM request_log) > 0                     AS log_present;
