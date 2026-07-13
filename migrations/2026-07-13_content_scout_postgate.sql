-- Content-Scout hard post-gate (2026-07-13)
-- post <id> refuses unless BOTH are set for the CURRENT text version (redraft_version):
--   verify_confirmed_version — primary-source fact-check recorded (content-scout verify-confirm)
--   approved_version         — explicit human sign-off recorded (content-scout approve)
-- A re-draft bumps redraft_version and nulls both, so a changed draft must be re-verified + re-approved.
ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS verify_confirmed_version integer;
ALTER TABLE content_review_queue ADD COLUMN IF NOT EXISTS approved_version integer;
