-- The 0/0 traffic figures are not measurements, and they are not fabricated
-- either: the record cannot tell the two apart.
--
-- Until 2026-10-05, discovery_snapshot.py wrote `t.get("count", 0)`, so a
-- failed traffic read became a zero no reader could distinguish from a repo
-- with no traffic. Four of six repos carry 0/0 in every row since 2026-05-21,
-- while moltrust-api and moltrust-web carry numbers that move day to day — so
-- the endpoint was answering, and those four zeros are *probably* real for
-- 1-star repos. Probably is not a measurement.
--
-- It cannot be resolved retroactively: `errors` was `[]`, no HTTP status was
-- kept, and the 14-day window those numbers covered has passed. So the
-- uncertainty goes into the data rather than into a footnote: each affected
-- field gets `*_14d_basis: "indeterminate"` beside it, and the row keeps its
-- numbers so nothing is invented in the other direction.
--
-- Rows with a non-zero value are left alone: those were read.

BEGIN;

-- One pass per row, rebuilding the github object. The first attempt joined
-- `FROM jsonb_each(...)` and set one repo per snapshot_at, so 58 of 236 fields
-- were marked and 178 were left — a correct-looking UPDATE count for an
-- incomplete write. jsonb_object_agg does every repo of a row at once, and
-- re-running sets the same keys, so it is idempotent.
UPDATE discovery_snapshots s
   SET payload = jsonb_set(s.payload, ARRAY['github'], g.neu, true)
  FROM (
    SELECT s2.snapshot_at,
           jsonb_object_agg(
             e.key,
             CASE WHEN (e.value->>'views_14d_count') = '0'
                   AND (e.value->>'clones_14d_count') = '0'
                  THEN e.value || '{"views_14d_basis": "indeterminate",
                                    "clones_14d_basis": "indeterminate"}'::jsonb
                  ELSE e.value END) AS neu
      FROM discovery_snapshots s2, jsonb_each(s2.payload->'github') e
     GROUP BY s2.snapshot_at
  ) g
 WHERE s.snapshot_at = g.snapshot_at;

-- Proof: every 0/0 field now carries its basis, and no non-zero field was touched.
SELECT count(*) AS ohne_basis_verblieben
  FROM discovery_snapshots s, jsonb_each(s.payload->'github') e
 WHERE (e.value->>'views_14d_count') = '0'
   AND (e.value->>'clones_14d_count') = '0'
   AND NOT (e.value ? 'views_14d_basis');

SELECT count(*) AS nichtnull_mit_basis
  FROM discovery_snapshots s, jsonb_each(s.payload->'github') e
 WHERE (e.value->>'views_14d_count') <> '0'
   AND (e.value ? 'views_14d_basis');

COMMIT;
