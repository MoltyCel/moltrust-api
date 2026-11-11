-- Archive of every issued credential, in our own database (ops IP-1).
--
-- From IP-1 on IPFS receives only the hash of a credential; the full signed
-- credential is kept here. `origin` says how a row got in: 'issued' at issuance,
-- 'ipfs-backfill' by scripts/vc_archive_backfill.py from a CID published
-- before IP-1. A separate table rather than columns on `credentials`, because
-- this role does not own that table, and because credentials of other types
-- (music, fantasy lineups) are archived here too.
--
-- Idempotent. Apply by hand before the deploy that ships app/vc_archive.py:
--   psql -h localhost -U moltstack -d moltstack -f <this file>

CREATE TABLE IF NOT EXISTS vc_archive (
    id              bigserial PRIMARY KEY,
    vc_hash         char(64) NOT NULL UNIQUE CHECK (vc_hash ~ '^[0-9a-f]{64}$'),
    credential_type varchar(64),
    subject_did     varchar(128),
    issuer          varchar(100),
    proof_value     text,
    raw_vc          jsonb NOT NULL,
    source          varchar(64) NOT NULL,
    origin          varchar(16) NOT NULL DEFAULT 'issued'
                    CHECK (origin IN ('issued', 'ipfs-backfill')),
    ipfs_cid        varchar(100),
    archived_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS vc_archive_proof_value_idx ON vc_archive (proof_value);
CREATE INDEX IF NOT EXISTS vc_archive_subject_did_idx ON vc_archive (subject_did);

COMMENT ON TABLE vc_archive IS
  'Every issued credential in full (ops IP-1). IPFS carries only vc_hash from IP-1 on.';
COMMENT ON COLUMN vc_archive.vc_hash IS
  'Hex SHA-256 over the RFC 8785 (JCS) form of the credential as signed.';
COMMENT ON COLUMN vc_archive.ipfs_cid IS
  'For origin issued: CID of the pinned hash document. For ipfs-backfill: the CID of the full credential published before IP-1.';
