-- caller_framework — attributes each request_log row to a MolTrust client SDK
-- by parsing the branded User-Agent (moltrust-crewai/x -> crewai,
-- moltrust-langchain/x -> langchain, moltrust-mcp-server/x -> mcp-server).
-- NULL for generic/browser/scanner callers. Powers the caller_framework signal
-- in /admin/dashboard/callers. 2026-07-01.
ALTER TABLE request_log ADD COLUMN IF NOT EXISTS caller_framework text;
CREATE INDEX IF NOT EXISTS idx_request_log_caller_framework
  ON request_log (caller_framework, ts DESC)
  WHERE caller_framework IS NOT NULL;
