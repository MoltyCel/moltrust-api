-- Fix: the content_scout lead-model (PR #259, commit bda0965) switched
-- draft_type to 'gh_lead', but the CHECK constraint created in
-- 2026-07-08_content_review_queue.sql still only allowed
-- ('gh_comment','blog_post','none'). Every pass-lead INSERT therefore raised
-- a CheckViolationError and — because it propagated out of the per-candidate
-- loop — silently killed the whole content_scout run 2x/day from 2026-07-14 on.
--
-- This widens the constraint additively to include 'gh_lead'. Idempotent
-- (DROP IF EXISTS + ADD), safe to re-run. No table rewrite (CHECK add only
-- validates existing rows; ~200 rows, all already valid).

ALTER TABLE content_review_queue
  DROP CONSTRAINT IF EXISTS content_review_queue_draft_type_check;

ALTER TABLE content_review_queue
  ADD CONSTRAINT content_review_queue_draft_type_check
  CHECK (draft_type IN ('gh_comment', 'blog_post', 'none', 'gh_lead'));
