-- Credentials that do not verify with the issuer keys we publish.
--
-- Found 2026-10-12: 14 credentials under did:web:api.moltrust.ch#key-1, issued
-- 2026-02-28 to 2026-03-23, verify in no tested canonicalization against the
-- public key in did.json (z6MktwcfvxeKmXstWpyEr9wJkJE2xzzkpBkdCSghdvCzrqDC),
-- with or without `evidence`. Tested: verify_proof as stored and without
-- evidence; the legacy body-without-proof json.dumps(sort_keys=True) of the
-- first commit (6c6a892) with and without evidence and a top-level id, in four
-- serializations; integral floats as int and back; the v1 context alone.
-- Report: ~/Downloads/block-1012/credential-pruefung.md, section 3.
--
-- Nothing is deleted and nothing is revoked: nobody revoked these, and a
-- revocation nobody declared would be a second false statement. The row marks
-- a finding. GET /identity/verify/{did} lists the credential with
-- verifiable: false, and the signed gate_attestation no longer counts its type.
--
-- `credentials` is postgres-owned and cannot take a column, so this table sits
-- beside it in a table this role owns. app.main.ensure_credential_verification_status_table
-- creates the same table at startup; this file is the record of it and the
-- form to apply by hand. Idempotent.

CREATE TABLE IF NOT EXISTS credential_verification_status (
    credential_id integer PRIMARY KEY REFERENCES credentials(id),
    status        text NOT NULL,
    reason        text NOT NULL,
    checked_at    timestamptz NOT NULL,
    checked_with  text NOT NULL
);

COMMENT ON TABLE credential_verification_status IS
  'Credentials that do not verify with the published issuer keys. A finding, not a revocation. Role-owned; credentials is postgres-owned.';

INSERT INTO credential_verification_status (credential_id, status, reason, checked_at, checked_with)
SELECT id,
       'not_verifiable_with_current_keys',
       CASE WHEN id <= 935
            THEN 'Issued before the first commit in the repository (2026-03-10); the canonicalization used at issuance is not on record. Verifies in no tested variant against the DID key.'
            ELSE 'Verifies against the DID key neither with the canonicalization of the code at issuance (6c6a892, 66f769a) nor in any other tested variant.'
       END,
       '2026-10-12T12:00:00Z',
       'did:web:api.moltrust.ch#key-1 z6MktwcfvxeKmXstWpyEr9wJkJE2xzzkpBkdCSghdvCzrqDC'
  FROM credentials
 WHERE id IN (925, 926, 927, 928, 929, 930, 931, 932, 933, 934, 935, 936, 937, 940)
ON CONFLICT (credential_id) DO NOTHING;

-- Expect 14.
SELECT count(*) AS marked FROM credential_verification_status;
