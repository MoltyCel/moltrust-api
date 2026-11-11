-- Growth mandate WM-1: what has been paid out of its budget by the cut.
-- Read-only. One JSON object on one line.
--
-- The ledger of record is ops/runden/budget.jsonl in MoltyCel/moltrust-security.
-- The server cannot read that private repo (the API token answers 404, measured
-- 2026-10-27), so the Monday line reads pool_spend, where every payment out of
-- a managed wallet is booked in the same step it is made (CLAUDE.md,
-- Kontenabgleich). Each WM-1 payment carries "Budget WM-1" in its purpose; on
-- 2026-10-27 both sides held the same 14 payments and 1.401198 USDC.
--
-- AS_OF is supplied by the caller (-v AS_OF=...).

SELECT json_build_object(
         'zahlungen', count(*),
         'ausgegeben', coalesce(sum(usdc), 0)::text)
  FROM pool_spend
 WHERE purpose LIKE '%Budget WM-1%'
   AND spent_at >= '2026-10-21T00:00:00Z'::timestamptz
   AND spent_at <  :'AS_OF'::timestamptz;
