-- Content-Scout lead model (2026-07-13)
-- lead_point: the one-line, primary-source-checkable point the worker surfaces per lead.
-- The worker no longer composes a comment (draft_md stays NULL for gh_lead rows) and never
-- posts; verify is done in review and the worker always marks a lead UNVERIFIED.
ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS lead_point text;
