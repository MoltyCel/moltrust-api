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

-- The task text prescribed platform='taskmarket' and 114 agents complied.
-- Thirteen did not, and set 'a2a', 'base' or 'moltbook' instead, while
-- registering inside the bounty window and then running exactly the task steps
-- against their own DID. Filtering on the platform string alone books those
-- thirteen as organic and inflates the very figure the 90-day goal is measured
-- against, so the cohort is the union of the declared and the observed.
WITH declared AS (
    SELECT did FROM agents
     WHERE platform = 'taskmarket'
       AND created_at < :cutoff::timestamptz
),
-- Observed: registered after the first bounty was created, and afterwards
-- retrieved its own DID through one of the endpoints the task named. That is
-- the task script, executed, whatever the agent called its platform.
observed AS (
    SELECT a.did FROM agents a
     WHERE a.created_at >= TIMESTAMP '2026-09-20 12:28'
       AND a.created_at < :cutoff::timestamptz
       AND a.platform <> 'taskmarket'
       AND EXISTS (
           SELECT 1 FROM request_log r
            WHERE (r.endpoint LIKE '%/skill/trust-score/' || a.did
                OR r.endpoint LIKE '%/identity/verify/' || a.did)
              AND r.status_code < 400)
),
-- A first attempt from the same /24 as an agent that did run the script is the
-- same operator, not a second interested party.
observed_sibling AS (
    SELECT a.did FROM agents a
      JOIN agents b ON b.registration_ip = a.registration_ip AND b.did <> a.did
     WHERE a.registration_ip IS NOT NULL
       AND a.created_at >= TIMESTAMP '2026-09-20 12:28'
       AND a.created_at < :cutoff::timestamptz
       AND a.platform <> 'taskmarket'
       AND b.did IN (SELECT did FROM observed)
),
cohort AS (
    SELECT did FROM declared
    UNION SELECT did FROM observed
    UNION SELECT did FROM observed_sibling
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
    (SELECT COUNT(*) FROM declared)                     AS dids_declared,
    (SELECT COUNT(*) FROM cohort) - (SELECT COUNT(*) FROM declared) AS dids_observed_only,
    (SELECT COUNT(*) FROM trust_score)                  AS trust_score_public,
    (SELECT COUNT(*) FROM verify)                       AS verify_public,
    (SELECT COUNT(*) FROM public_call)                  AS any_public_call,
    (SELECT COUNT(*) FROM api_key_bound)                AS api_key_bound,
    (SELECT COUNT(*) FROM authenticated)                AS any_authenticated_call,
    (SELECT COUNT(*) FROM off_script)                   AS off_script,
    (SELECT COUNT(*) FROM cohort
      WHERE did NOT IN (SELECT did FROM public_call)
        AND did NOT IN (SELECT did FROM authenticated)) AS no_request_at_all;

-- What "public call" measures, and what it does not. A row whose endpoint ends
-- with the DID says the DID was *retrieved*. During the task the agent retrieved
-- its own, so retrieval and action coincided. Afterwards they do not: on
-- 2026-09-21 three cohort DIDs appear in the log after the 14:45 close and all
-- three were looked up by third parties, while none of the cohort made an
-- authenticated call. Quote this column as "was looked up", never as "was
-- active".

-- Completeness of the window: request_log must start before the first
-- registration, otherwise every "no request" figure above is an artefact of
-- retention rather than a finding.
SELECT
    (SELECT MIN(ts) FROM request_log)                                        AS log_starts,
    (SELECT MIN(created_at) FROM agents WHERE platform = 'taskmarket')       AS first_declared_registration,
    (SELECT MAX(created_at) FROM agents WHERE platform = 'taskmarket')       AS last_declared_registration,
    (SELECT MIN(ts) FROM request_log)
        < (SELECT MIN(created_at) FROM agents WHERE platform = 'taskmarket') AS window_covers_cohort;
