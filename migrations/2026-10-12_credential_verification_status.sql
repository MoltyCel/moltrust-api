-- Ops C1 (2026-10-12). Credentials that do not verify with the issuer keys, marked
-- rather than deleted or revoked: nobody declared a revocation, and the documents
-- stay as they were issued. `credentials` belongs to postgres and the app role
-- cannot alter it, so the mark lives in a table of its own (no foreign key: the
-- app role has no REFERENCES on credentials). Additive and idempotent.

CREATE TABLE IF NOT EXISTS credential_verification_status (
    credential_id  INTEGER     PRIMARY KEY,
    status         TEXT        NOT NULL,
    reason         TEXT        NOT NULL,
    checked_at     TIMESTAMPTZ NOT NULL,
    checked_with   TEXT        NOT NULL
);

-- The 14 that verified in no tested variant (credential-pruefung, 12.10.2026).
INSERT INTO credential_verification_status (credential_id, status, reason, checked_at, checked_with)
SELECT id, 'not_verifiable_with_current_keys',
       CASE WHEN id BETWEEN 925 AND 935 THEN
            'issued before the first commit in the repository (2026-03-10); the canonical form '
            'used then cannot be established; verifies in no tested variant against the DID key'
       ELSE
            'verifies neither with the canonical form of the code of its day (6c6a892, 66f769a) '
            'nor with any other tested variant against the DID key'
       END,
       TIMESTAMPTZ '2026-10-12 12:13:00+00',
       'did:web:api.moltrust.ch#key-ed25519 / #key-1, publicKeyMultibase '
       'z6MktwcfvxeKmXstWpyEr9wJkJE2xzzkpBkdCSghdvCzrqDC'
  FROM credentials
 WHERE id IN (925, 926, 927, 928, 929, 930, 931, 932, 933, 934, 935, 936, 937, 940)
ON CONFLICT (credential_id) DO NOTHING;
