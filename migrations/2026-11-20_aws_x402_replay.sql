-- AWS-X402 v2 (20.11.2026): replay records shared by all API instances.
-- Also created on first use by app/aws_x402/stores.py (CREATE IF NOT EXISTS);
-- role-owned tables, no ALTER on postgres-owned ones.
CREATE TABLE IF NOT EXISTS aws_x402_payments (
    payment_id  text PRIMARY KEY,
    expires_at  timestamptz NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS aws_x402_dpop_jti (
    jti         text PRIMARY KEY,
    expires_at  timestamptz NOT NULL
);
