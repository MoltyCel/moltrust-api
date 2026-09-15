-- Phase 0 item B (feat/contact-endpoint): server-side intake for the
-- moltrust.ch contact form. The form was a client-side `mailto:` composer, so
-- no enquiry was ever recorded; this is the table POST /contact writes to.
--
-- Additive + idempotent; no drops -> code-rollback needs no DB rollback.
-- Created fresh by the `moltstack` role: no ALTER against a postgres-owned
-- table is involved, so this applies without superuser rights.
--
-- `ip` holds the /24-anonymised address (app._anonymize_ip), never the full
-- one. `mail_sent` is FALSE until the Infomaniak notification is accepted, so
-- a mail outage leaves a queryable backlog instead of losing the enquiry.
-- `is_spam` is for later manual or heuristic classification of stored rows;
-- honeypot hits are not stored at all.
CREATE TABLE IF NOT EXISTS contact_inbox (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    email       TEXT        NOT NULL,
    topic       TEXT        NOT NULL,
    message     TEXT        NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip          TEXT,
    user_agent  TEXT,
    mail_sent   BOOLEAN     NOT NULL DEFAULT FALSE,
    is_spam     BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_contact_inbox_received
    ON contact_inbox (received_at DESC);

-- Partial index: "which enquiries never got their notification mail out?"
CREATE INDEX IF NOT EXISTS idx_contact_inbox_unsent
    ON contact_inbox (received_at DESC) WHERE mail_sent = FALSE;
