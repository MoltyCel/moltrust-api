-- Content-Scout pipeline pass 2026-07-13
-- FIX 3: notified_at — set when a pending draft has been pushed to Telegram (one-way),
--        so re-runs don't resend (de-dup flag).
-- FIX 5: code_flag  — 'needs-code-verification' when a gh_comment draft embeds a fenced
--        code block; the `post` command refuses it until cleared (run code or label it).
ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS notified_at timestamptz;
ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS code_flag text NOT NULL DEFAULT 'none';
