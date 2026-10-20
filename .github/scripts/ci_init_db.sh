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
# The subject of every credential is a registered agent: production carries
# credentials_subject_did_fkey, validated, and no file in this repository
# creates it. Without it here, an insert for an unregistered subject passed in
# CI and ended in a 500 (ForeignKeyViolationError) on the box — POST
# /canary/issue did exactly that until ops DC-1. Same name, same definition as
# live, so a test against this schema fails where production fails.
q -v ON_ERROR_STOP=1 -c "ALTER TABLE credentials ADD CONSTRAINT credentials_subject_did_fkey FOREIGN KEY (subject_did) REFERENCES agents(did);"
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
# tests/test_ci_schema_trust_cache.py. (Since 2026-10-20 the endorsements
# table is rebuilt below and the migration then runs whole; the copy stays so
# the guard keeps checking the same thing.)
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
# endorsements in the shape the app writes and reads (vertical, weight,
# issued_at, expires_at — app/swarm/trust_score.py, app/swarm/endorsement.py).
# init_db.sql's older shape has none of them, so every trust-gate call ended
# in "column vertical does not exist". Nothing reads init_db's shape, and the
# CI database is ephemeral: dropped and rebuilt from the migration, which then
# runs whole (its trust_score_cache part is a no-op after the copy above).
q -v ON_ERROR_STOP=1 -c "DROP TABLE IF EXISTS endorsements CASCADE;"
q -v ON_ERROR_STOP=1 -f migrations/001_swarm_phase1.sql
q -f migrations/2026-09-20_credential_anchoring.sql
# interaction_proof_records is created by the app at startup
# (app/provenance/ipr.py, ENSURE_TABLE_SQL) and by no migration. The parity
# migration below adds columns to it, so it has to exist first. The DDL is
# read out of the source with ast rather than by importing the module, so the
# schema step does not depend on the app importing cleanly.
python3 - <<'PY' | q -v ON_ERROR_STOP=1
import ast
tree = ast.parse(open("app/provenance/ipr.py").read())
for node in tree.body:
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ENSURE_TABLE_SQL" for t in node.targets):
        print(node.value.value)
        break
else:
    raise SystemExit("ENSURE_TABLE_SQL not found in app/provenance/ipr.py")
PY
q -f migrations/2026-09-20_ipr_immutability.sql || true
# The tables live has and no other migration creates (agent_delegations,
# swarm_seeds, request_log among them), generated from the live schema on
# 2026-10-08 and idempotent. It runs before anything else creates request_log:
# the minimal request_log this file used to build (no `ts`, no `agent_did`, no
# `caller_framework`) made this migration fail on its request_log indexes, and
# made every middleware INSERT fail against a column live has. Without it
# tests/test_trust_gate.py ends in "relation swarm_seeds does not exist" and
# /identity/revoke in "relation agent_delegations does not exist".
q -v ON_ERROR_STOP=1 -f migrations/2026-10-08_sandbox_schema_parity.sql
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
# The key columns of agents as production has them. init_db.sql has neither,
# so every suite that registers a keyed agent (acceptance gate, subject
# binding, AAE endpoints) ended in "column public_key_hex does not exist".
q -v ON_ERROR_STOP=1 -c "ALTER TABLE agents ADD COLUMN IF NOT EXISTS public_key_hex VARCHAR, ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP, ADD COLUMN IF NOT EXISTS revocation_reason VARCHAR, ADD COLUMN IF NOT EXISTS key_anchor_tx VARCHAR, ADD COLUMN IF NOT EXISTS key_anchor_block BIGINT;"
q -v ON_ERROR_STOP=1 -f migrations/2026-10-08_agent_key_history.sql
q -v ON_ERROR_STOP=1 -f migrations/2026-09-04_aae_subject_nonces.sql
# Ops V6: request-signature nonce store and aae_evaluations.request_proof.
q -v ON_ERROR_STOP=1 -f migrations/2026-10-18_v6_request_signature.sql
# Columns the trust gate reads from trust_score_cache that live carries and
# migrations/001_swarm_phase1.sql does not (app/swarm/trust_score.py selects
# and upserts them). Added here, not in the CREATE TABLE copy above, so that
# copy stays identical to the migration (tests/test_ci_schema_trust_cache.py).
q -v ON_ERROR_STOP=1 -c "ALTER TABLE trust_score_cache ADD COLUMN IF NOT EXISTS propagated_score REAL, ADD COLUMN IF NOT EXISTS cross_vertical_bonus REAL, ADD COLUMN IF NOT EXISTS computation_method TEXT;"
# agents.cold_start_* (F2 cold-start score); the trust gate reads them.
q -v ON_ERROR_STOP=1 -f app/migrations/007_cold_start_score.sql
# CAEP events in the shape app/caep.py writes (event_id, expires_at,
# acknowledged_at). It has to exist before the app starts: ensure_caep_table()
# in app/main.py creates an older shape without those columns when the table
# is missing, and every CAEP test then ends in "column expires_at does not
# exist". gen_random_bytes comes from pgcrypto.
q -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;"
q -v ON_ERROR_STOP=1 -f migrations/2026-05-09_caep_events.sql
# api_keys.email_normalized and email_path_registrations (signup dedup).
q -v ON_ERROR_STOP=1 -f app/migrations/2026-07-11_email_path_sybil_cost.sql
# Content-Scout review queue, in migration order.
q -v ON_ERROR_STOP=1 -f migrations/2026-07-08_content_review_queue.sql
q -v ON_ERROR_STOP=1 -f migrations/2026-07-13_content_scout_lead_model.sql
q -v ON_ERROR_STOP=1 -f migrations/2026-07-13_content_scout_notify_codeflag.sql
q -v ON_ERROR_STOP=1 -f migrations/2026-07-13_content_scout_postgate.sql
q -v ON_ERROR_STOP=1 -f migrations/2026-07-13_content_scout_redraft_versioning.sql
q -v ON_ERROR_STOP=1 -f migrations/2026-07-16_content_scout_lead_constraint_fix.sql
# agents.registration_ip, which live has and no migration adds (the email-path
# migration above says so: "reads the existing agents.registration_ip").
# /identity/register writes it and the per-/24 signup cap reads it.
q -v ON_ERROR_STOP=1 -c "ALTER TABLE agents ADD COLUMN IF NOT EXISTS registration_ip TEXT;"
