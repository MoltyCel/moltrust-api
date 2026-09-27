-- What each channel actually brought, for the weekly report.
--
-- Two columns that are easy to confuse. `platform` is what the agent says it
-- belongs to; `source` is which of our surfaces produced the registration. A
-- bounty agent and an agent that followed a 402 hint can both send
-- platform='taskmarket'. Only `source` answers "was the channel worth it".
--
-- Attribution is self-reported and optional, so untagged registrations are
-- shown as their own row rather than folded into a channel. A channel's yield
-- is a floor, never a total.
--
--   psql -h localhost -U moltstack -d moltstack -X -f app/sql/channel_yield.sql

\set ON_ERROR_STOP on

\echo '== registrations per channel, last 30 days =='
SELECT coalesce(s.source, '(untagged)')                       AS channel,
       count(*)                                               AS registered,
       count(*) FILTER (WHERE a.wallet_address IS NOT NULL)    AS bound_a_wallet,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM credentials c
             JOIN credential_anchors k ON k.credential_id = c.id
            WHERE c.subject_did = a.did AND NOT c.revoked))     AS anchored,
       min(a.created_at)::date                                 AS first_seen,
       max(a.created_at)::date                                 AS last_seen
  FROM agents a
  LEFT JOIN agent_source s ON s.did = a.did
 WHERE a.revoked_at IS NULL
   AND a.created_at >= now() - interval '30 days'
 GROUP BY 1
 ORDER BY 2 DESC;

\echo '== the same channels, but did they come back =='
-- request_log holds 30 days, so this is recurrence inside that window only.
SELECT coalesce(s.source, '(untagged)') AS channel,
       count(DISTINCT a.did)            AS agents,
       count(DISTINCT a.did) FILTER (WHERE d.days > 1) AS more_than_one_call_day
  FROM agents a
  LEFT JOIN agent_source s ON s.did = a.did
  LEFT JOIN (SELECT agent_did, count(DISTINCT ts::date) AS days
               FROM request_log WHERE agent_did IS NOT NULL GROUP BY 1) d
         ON d.agent_did = a.did
 WHERE a.revoked_at IS NULL
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== completeness =='
SELECT (SELECT count(*) FROM agent_source) AS tagged_rows,
       (SELECT count(*) FROM agents WHERE revoked_at IS NULL) AS live_agents;
