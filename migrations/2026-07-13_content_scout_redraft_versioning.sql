-- Content-Scout re-draft versioning (2026-07-13)
-- redraft_version:      bumped on each in-place re-draft (`content-scout redraft <id>`).
-- telegram_message_ids: JSON array of the Telegram message_id(s) of the row's last push.
--   Recorded only (stage 1) — the prerequisite for a later editMessageText-in-place pass;
--   no edit/delete logic is built yet.
ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS redraft_version integer NOT NULL DEFAULT 1;
ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS telegram_message_ids jsonb;
