-- Canonical agent counting rules.
--
-- One file, read by both /stats (app/main.py) and the 07:00/19:00 digest
-- (scripts/daily_stats.sh). Keeping the predicates in two places is how the two
-- drifted apart: /stats counted every row (99) while the digest dropped anything
-- whose display name contained probe/test/ambassador (72), a heuristic that hid
-- 19 real partner registrations and still let 4 agents on platform 'test'
-- through.
--
--   registered           every DID still on the books. Nothing is ever deleted
--                        and there is no status column, so "not revoked" is the
--                        whole of it.
--   active               distinct DIDs with at least one API-key-authenticated
--                        call in the window. Source is usage_daily_keys, the
--                        rollup that outlives the 30-day request_log pruning.
--   active_window_days   caller identity has only been recorded since
--                        2026-09-14 (#342), so the window grows with the data
--                        instead of printing "30d" over four days of it. Reaches
--                        the full 30 on 2026-10-14.
--   test                 our own test surface: platform 'test' plus the system
--                        agents.
--   partner_test         registered agents whose display name matches the old
--                        heuristic. Informational only -- these are genuine
--                        partner registrations and are deducted nowhere.
--
-- test and partner_test are both subsets of registered; they do not sum with it.
WITH active_window AS (
    SELECT COALESCE(LEAST(30, GREATEST(1, current_date - MIN(day))), 30)::int AS days
    FROM usage_daily_keys
)
SELECT
    (SELECT COUNT(*) FROM agents
      WHERE revoked_at IS NULL) AS registered,

    (SELECT COUNT(DISTINCT k.did) FROM usage_daily_keys k
      WHERE k.did IS NOT NULL
        AND k.day > current_date - (SELECT days FROM active_window)) AS active,

    (SELECT days FROM active_window) AS active_window_days,

    (SELECT COUNT(*) FROM agents
      WHERE revoked_at IS NULL
        AND (platform = 'test' OR agent_type = 'system')) AS test,

    (SELECT COUNT(*) FROM agents
      WHERE revoked_at IS NULL
        AND NOT (platform = 'test' OR agent_type = 'system')
        AND (lower(display_name) LIKE '%test%'
          OR lower(display_name) LIKE '%probe%'
          OR lower(display_name) LIKE '%ambassador%')) AS partner_test;
