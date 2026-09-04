-- AAE §5 Step 4 (subject-binding challenge-response): used-nonce store.
--
-- The challenge nonce carries its own origin proof (HMAC), so issuing one needs
-- no database round trip. What the HMAC cannot express is single use: condition
-- (c) of Step 4 requires that a nonce has not been used before. That is a
-- database invariant here — nonce_hash is the primary key, so a concurrent
-- replay loses the insert and is rejected.
--
-- Additive: no existing table is touched. aae_evaluations.nonce keeps its own
-- meaning (client-supplied evaluator replay token) and is unrelated to this store.

CREATE TABLE IF NOT EXISTS aae_subject_nonces (
    nonce_hash   bytea       PRIMARY KEY,
    aae_id       text        NOT NULL,
    aud          text        NOT NULL,
    subject_did  text        NOT NULL,
    used_at      timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);

-- Cleanup path: rows are only useful until the nonce would have expired anyway.
CREATE INDEX IF NOT EXISTS idx_aae_subject_nonces_expires
    ON aae_subject_nonces (expires_at);

COMMENT ON TABLE aae_subject_nonces IS
    'Used subject-binding nonces (AAE §5 Step 4, condition c). Rows past expires_at are deletable.';
