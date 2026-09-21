-- Canonical count of the taskmarket bounty cohort.
--
-- One query, one DID set, one time window. Every figure the blog, the Weekly
-- Proof or a Telegram line quotes about this cohort comes from here, so that
-- two reports cannot answer two different questions under the same word.
--
-- Definitions are binding and live in CLAUDE.md, section
-- "90-Tage-Ziel: Zählregel". In short: the DID set is every agent with
-- platform='taskmarket', a public call is a request_log row whose endpoint ends
-- with the DID and returned < 400, an authenticated call is a row where the
-- server resolved agent_did, and off-script means an authenticated call to an
-- endpoint no task text named.
--
-- Two earlier reports disagreed without either being wrong:
--   "112 DIDs, 99 without a call"  — the cohort as of 2026-09-21 13:42, counting
--                                    authenticated calls, and then labelled
--                                    "never made a request", which was false.
--   "114 DIDs, 100 with a call"    — the cohort at 15:03, counting public
--                                    trust-score retrievals.
-- Both reproduce exactly from this query by moving the cutoff. The figure that
-- was actually wrong is neither: agents that made no request at all number 3.
--
--   psql -h localhost -U moltstack -d moltstack -f app/sql/taskmarket_cohort.sql
--
-- No LIMIT anywhere on purpose — see CLAUDE.md, "Vollständigkeit beim Lesen".

\set cutoff 'now()'

WITH cohort AS (
    SELECT did FROM agents
     WHERE platform = 'taskmarket'
       AND created_at < :cutoff::timestamptz
),
public_call AS (
    SELECT c.did FROM cohort c
      JOIN request_log r ON r.endpoint LIKE '%' || c.did
     WHERE r.status_code < 400
     GROUP BY 1
),
trust_score AS (
    SELECT c.did FROM cohort c
      JOIN request_log r ON r.endpoint LIKE '%/skill/trust-score/' || c.did
     WHERE r.status_code < 400
     GROUP BY 1
),
verify AS (
    SELECT c.did FROM cohort c
      JOIN request_log r ON r.endpoint LIKE '%/identity/verify/' || c.did
     WHERE r.status_code < 400
     GROUP BY 1
),
api_key_bound AS (
    SELECT c.did FROM cohort c JOIN api_keys k ON k.owner_did = c.did GROUP BY 1
),
authenticated AS (
    SELECT c.did FROM cohort c JOIN request_log r ON r.agent_did = c.did GROUP BY 1
),
off_script AS (
    SELECT c.did FROM cohort c
      JOIN request_log r ON r.agent_did = c.did
     WHERE r.endpoint NOT LIKE '%/identity/verify/%'
       AND r.endpoint NOT LIKE '%/skill/trust-score/%'
       AND r.endpoint NOT LIKE '%/identity/erc8004/register%'
     GROUP BY 1
)
SELECT
    (SELECT COUNT(*) FROM cohort)                       AS dids_registered,
    (SELECT COUNT(*) FROM trust_score)                  AS trust_score_public,
    (SELECT COUNT(*) FROM verify)                       AS verify_public,
    (SELECT COUNT(*) FROM public_call)                  AS any_public_call,
    (SELECT COUNT(*) FROM api_key_bound)                AS api_key_bound,
    (SELECT COUNT(*) FROM authenticated)                AS any_authenticated_call,
    (SELECT COUNT(*) FROM off_script)                   AS off_script,
    (SELECT COUNT(*) FROM cohort
      WHERE did NOT IN (SELECT did FROM public_call)
        AND did NOT IN (SELECT did FROM authenticated)) AS no_request_at_all;

-- Completeness of the window: request_log must start before the first
-- registration, otherwise every "no request" figure above is an artefact of
-- retention rather than a finding.
SELECT
    (SELECT MIN(ts) FROM request_log)                                        AS log_starts,
    (SELECT MIN(created_at) FROM agents WHERE platform = 'taskmarket')       AS first_registration,
    (SELECT MAX(created_at) FROM agents WHERE platform = 'taskmarket')       AS last_registration,
    (SELECT MIN(ts) FROM request_log)
        < (SELECT MIN(created_at) FROM agents WHERE platform = 'taskmarket') AS window_covers_cohort;
