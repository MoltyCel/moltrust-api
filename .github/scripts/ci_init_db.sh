#!/bin/sh
# Build the CI test database. Both the credit-middleware job and the whole-
# suite job need the same schema, and two copies of this block drift: the
# second one is always the one nobody updates. Runs against an ephemeral
# container, so PGPASSWORD in the environment is fine.
set -eu

DB=moltstack_sandbox
q() { psql -h localhost -U moltstack -d "$DB" "$@"; }

q -f init_db.sql
# CI schema alignment: columns/tables present on live DB but missing from init_db.sql
q -c "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS email TEXT;"
# api_keys as production actually has it. init_db.sql declares
# owner_did NOT NULL; live has 23 of 90 keys with a NULL owner_did —
# every key minted by email signup before an agent exists. A test
# could not reproduce that state, which is how the 402-instead-of-401
# path stayed untested. email became nullable with the signature
# signup path (migrations/2026-09-19_free_tier_signup_by_signature).
q -c "ALTER TABLE api_keys ALTER COLUMN owner_did DROP NOT NULL;"
q -c "ALTER TABLE api_keys ALTER COLUMN email DROP NOT NULL;"
q -c "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS signup_method TEXT;"
# credentials as production has it. /identity/verify reads `revoked`
# and the anchor columns; init_db.sql carries neither, so the endpoint
# 500s in CI against a table that is fine on the live box.
# init_db.sql gives credentials a UUID id; live has INTEGER. The
# anchor side table joins on that id, so a UUID here turns into
# "operator does not exist: integer = uuid" against a schema that is
# fine on the box. Dropped and rebuilt to match live — the CI database
# is ephemeral and nothing reads init_db's shape of this table.
q -c "DROP TABLE IF EXISTS credentials CASCADE;"
q -c "CREATE TABLE credentials (id SERIAL PRIMARY KEY, subject_did TEXT, credential_type TEXT, issuer TEXT, issued_at TIMESTAMPTZ, expires_at TIMESTAMPTZ, proof_value TEXT, raw_vc JSONB, authorization_envelope JSONB, ipfs_cid TEXT);"
q -c "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS revoked BOOLEAN DEFAULT FALSE;"
q -c "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;"
# trust_score_cache, from migrations/001_swarm_phase1.sql. The trust gate
# reads it on every call, so without the table tests/test_trust_gate.py ends
# in "relation does not exist" — the suite was in no job until 2026-10-17, so
# the gap could not show.
#
# The migration itself cannot run here: it also carries `endorsements`, which
# init_db.sql above already created in a different shape, and its
# CREATE TABLE IF NOT EXISTS is therefore skipped while the index that
# follows looks for a column (expires_at) the existing table has not got.
# Copied rather than included for that reason, which is the drift this file
# otherwise exists to prevent — so the two are kept identical by
# tests/test_ci_schema_trust_cache.py.
q -v ON_ERROR_STOP=1 -c "CREATE TABLE IF NOT EXISTS trust_score_cache (
  did               TEXT PRIMARY KEY,
  score             REAL NOT NULL,
  endorser_count    INTEGER NOT NULL,
  sybil_penalty     REAL NOT NULL DEFAULT 0.0,
  computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  cache_valid_until TIMESTAMPTZ NOT NULL
);"
q -v ON_ERROR_STOP=1 -c "CREATE INDEX IF NOT EXISTS idx_trust_score_cache_valid
  ON trust_score_cache(cache_valid_until);"
q -f migrations/2026-09-20_credential_anchoring.sql
q -f migrations/2026-09-20_ipr_immutability.sql || true
# request_log is referenced by middleware during test runs. Table not in
# init_db.sql — add minimal schema so INSERTs don't flood the log.
q -c "CREATE TABLE IF NOT EXISTS request_log (id BIGSERIAL PRIMARY KEY, endpoint TEXT, method TEXT, status_code INT, ip TEXT, user_agent TEXT, response_ms FLOAT, source TEXT, ip_org TEXT, ip_country TEXT, created_at TIMESTAMPTZ DEFAULT NOW());"
# PR #81 wired record_spend_event() into the credit-deduct path; that
# call needs agents.operator_did + agent_budget_caps + budget_spend_events.
# 009 has a FK on gate_events(id) so 008 must run first. Both migrations
# use IF NOT EXISTS / ADD COLUMN IF NOT EXISTS — idempotent.
q -v ON_ERROR_STOP=1 -f app/migrations/008_gate_events.sql
q -v ON_ERROR_STOP=1 -f app/migrations/009_agent_budget_caps.sql
# 010: D3 aae_envelopes store (component 1). Idempotent (IF NOT EXISTS /
# OR REPLACE / DROP-before-CREATE TRIGGER), additiv, kein ALTER an Bestand.
q -v ON_ERROR_STOP=1 -f app/migrations/010_aae_envelopes.sql
# 011: D3 aae_evaluations (component 2 evaluator store + signed audit trail).
# Append-only; FK aae_ref -> 010 muss zuerst laufen. Idempotent.
q -v ON_ERROR_STOP=1 -f app/migrations/011_aae_evaluations.sql
# 012: drop FK aae_evaluations.aae_ref (FK-lock vs immutable-store REVOKE conflict);
# sha256-CHECK stays. Idempotent.
q -v ON_ERROR_STOP=1 -f app/migrations/012_drop_aae_eval_fk.sql
# 013: D-1 issuer_trust_tier column on aae_envelopes (additive). Idempotent.
q -v ON_ERROR_STOP=1 -f app/migrations/013_issuer_trust_tier.sql
# 2026-06-15: insufficient_credit_events — caller_did-on-402 metric (dashboard_overview reads it; additive, IF NOT EXISTS).
q -v ON_ERROR_STOP=1 -f app/migrations/2026-06-15_insufficient_credit_events.sql
