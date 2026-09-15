-- Content-Scout reach filter + hold list (2026-09-15)
-- NOT applied automatically — apply by hand, then the worker uses the new shape.
-- Until applied, the worker falls back to the legacy row shape: a held item is
-- stored as 'discarded' with the hold-list entry ID in class_reason, and the reach
-- numbers are folded into class_reason as well (it never becomes a post candidate).
--
-- 1. state 'held': PASS/WATCH items that matched the (out-of-repo) hold list.
--    Queue row only — never carded to Telegram, never a post candidate.
-- 2. reach jsonb: {stars, contributors, tier, label, bypass, reason, error, hold}
--    hold = matched hold-list entry ID or a fail-safe marker; never the list text.
--
-- Idempotent (DROP IF EXISTS + ADD; ADD COLUMN IF NOT EXISTS). Additive only:
-- existing rows all satisfy the widened CHECK; no table rewrite.

ALTER TABLE content_review_queue
  DROP CONSTRAINT IF EXISTS content_review_queue_state_check;

ALTER TABLE content_review_queue
  ADD CONSTRAINT content_review_queue_state_check
  CHECK (state IN ('pending_review', 'approved', 'discarded', 'published', 'held'));

ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS reach jsonb;
