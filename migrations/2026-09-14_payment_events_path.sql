-- payment_events.path — which endpoint a settlement paid for.
--
-- REQUIRES THE postgres ROLE. payment_events is owned by postgres and the
-- application role moltstack may not ALTER it:
--
--   moltstack=> ALTER TABLE payment_events ADD COLUMN path TEXT;
--   ERROR:  must be owner of table payment_events
--
-- Apply as:
--   sudo -u postgres psql -d moltstack -f 2026-09-14_payment_events_path.sql
--
-- Until it is applied, MoltGuard writes the row without the column: the insert
-- is attempted with `path`, and a 42703 (undefined_column) falls back to the
-- older shape rather than dropping a real payment over a reporting field. The
-- fallback is remembered for the process lifetime, so the cost is one failed
-- statement per restart, and it disappears once this runs.

ALTER TABLE payment_events ADD COLUMN IF NOT EXISTS path TEXT;

-- Both payment paths write the same row, so the column answers "what was
-- bought" across direct transfers and EIP-3009 settlements alike.
CREATE INDEX IF NOT EXISTS idx_payment_events_path ON payment_events (path);

-- The application role needs no new grant: INSERT and UPDATE on the table are
-- already held, and a new column inherits them.
