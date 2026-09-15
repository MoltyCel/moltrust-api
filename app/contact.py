"""Contact-form intake — validation, persistence and notification mail.

Until now moltrust.ch/contact.html was a pure client-side ``mailto:``
composer: it validated four fields and then set ``window.location.href`` to a
``mailto:`` URL. Nothing ever reached a server, so nothing was ever recorded —
and on a device with no registered ``mailto:`` handler the submit button did
nothing at all. This module is the server side of that form.

Ordering is deliberate: the row is written first and the notification mail is
attempted afterwards. An SMTP outage then costs a notification, never the
enquiry itself, and ``contact_inbox.mail_sent`` says which rows a human still
has to read out of the table.

The route lives in ``app/main.py`` because that is where the shared slowapi
limiter is defined; everything else it needs is here.
"""

import logging
import re
import uuid

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("moltrust")

# Where genuine submissions are announced. Not the public info@ address: this
# is the human who actually triages enquiries.
CONTACT_RECIPIENT = "kersten.kroehl@cryptokri.ch"

# Mirrors the client-side rules in contact.html, with server-side upper bounds
# the browser does not enforce.
MIN_MESSAGE_LEN = 20
MAX_MESSAGE_LEN = 5000
MAX_NAME_LEN = 120
MAX_EMAIL_LEN = 254          # RFC 5321 maximum path length
MAX_TOPIC_LEN = 64
MAX_HONEYPOT_LEN = 256       # bounded so a bot cannot post megabytes into it

# Deliberately conservative: ASCII-only local and domain parts. Anything this
# rejects is also rejected by `<input type="email">`, so a real visitor who got
# through the browser check gets through this one.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Strip C0/C1 control characters, but keep tab, LF and CR — a message body
# legitimately contains newlines. Header-bearing fields reject CR/LF
# separately below, so nothing user-supplied can inject a mail header.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# The one response body the endpoint ever returns on the happy path. A caught
# bot and a genuine visitor must receive byte-identical output; see
# `is_honeypot_filled`.
ACCEPTED_RESPONSE = {
    "status": "received",
    "message": "Thanks — we typically respond within one business day.",
}


class ContactRequest(BaseModel):
    """A contact-form submission.

    ``website`` is the honeypot. It is named after a field a form-filling bot
    expects to find and will happily complete; the real form renders it
    hidden, empty and out of the tab order. It is never echoed back and never
    appears in an error message.
    """

    name: str = Field(max_length=MAX_NAME_LEN)
    email: str = Field(max_length=MAX_EMAIL_LEN)
    topic: str = Field(max_length=MAX_TOPIC_LEN)
    message: str = Field(max_length=MAX_MESSAGE_LEN)
    website: str = Field(default="", max_length=MAX_HONEYPOT_LEN)

    @field_validator("name", "email", "topic", "message", "website", mode="before")
    @classmethod
    def _clean(cls, v):
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return _CONTROL_RE.sub("", v).strip()

    @field_validator("name", "email", "topic")
    @classmethod
    def _single_line_and_present(cls, v: str) -> str:
        # CR/LF in a field that ends up in a Subject or Reply-To header is the
        # classic mail-header-injection vector.
        if "\n" in v or "\r" in v:
            raise ValueError("must not contain line breaks")
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("message")
    @classmethod
    def _message_long_enough(cls, v: str) -> str:
        if len(v) < MIN_MESSAGE_LEN:
            raise ValueError(f"must be at least {MIN_MESSAGE_LEN} characters")
        return v


def is_honeypot_filled(payload: ContactRequest) -> bool:
    """True when the hidden field carries anything a human would not type."""
    return bool(payload.website)


async def ensure_contact_tables(conn) -> None:
    """Create `contact_inbox` if it is missing.

    Same DDL as migrations/2026-09-15_contact_inbox.sql. Additive and
    idempotent, and the table is created fresh by the `moltstack` role, so no
    ALTER against a postgres-owned table is involved.
    """
    await conn.execute(
        """
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
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_inbox_received "
        "ON contact_inbox (received_at DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_inbox_unsent "
        "ON contact_inbox (received_at DESC) WHERE mail_sent = FALSE"
    )


async def store_submission(conn, payload: ContactRequest, ip: str, user_agent: str) -> uuid.UUID:
    """Persist one submission and return its id. Values are bound, never interpolated."""
    row = await conn.fetchrow(
        "INSERT INTO contact_inbox (name, email, topic, message, ip, user_agent) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        payload.name,
        payload.email,
        payload.topic,
        payload.message,
        ip,
        user_agent,
    )
    return row["id"]


async def mark_mail_sent(conn, submission_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE contact_inbox SET mail_sent = TRUE WHERE id = $1", submission_id
    )


async def send_contact_notification(
    payload: ContactRequest, submission_id: uuid.UUID, ip: str, user_agent: str
) -> bool:
    """Notify the triage address. Returns True only if SMTP accepted the message.

    Uses the same Infomaniak submission path and the same credential handling
    as the signup welcome mail (`send_welcome_email` in app/main.py) — there is
    no MTA on the host, so this is the only way mail leaves the box.
    """
    from app.main import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS

    if not SMTP_PASS:
        logger.warning(
            "SMTP_PASS not set, skipping contact notification for %s", submission_id
        )
        return False
    try:
        import aiosmtplib
        from email.header import Header
        from email.mime.text import MIMEText

        body = (
            f"New contact-form submission on moltrust.ch\n"
            f"\n"
            f"Topic:   {payload.topic}\n"
            f"Name:    {payload.name}\n"
            f"Email:   {payload.email}\n"
            f"\n"
            f"Message:\n"
            f"{payload.message}\n"
            f"\n"
            f"---\n"
            f"Submission ID: {submission_id}\n"
            f"Source IP (/24): {ip}\n"
            f"User agent: {user_agent}\n"
            f"Stored in: contact_inbox\n"
        )

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = f"MolTrust Contact <{SMTP_USER}>"
        msg["To"] = CONTACT_RECIPIENT
        # Reply-To carries the visitor's address so a reply goes straight back
        # to them. Validated above to be a single-line ASCII address.
        msg["Reply-To"] = payload.email
        # Name and topic are visitor-supplied and may be non-ASCII; Header
        # encodes them as RFC 2047 words instead of emitting raw UTF-8.
        msg["Subject"] = str(
            Header(f"[MolTrust Contact] {payload.topic} - {payload.name}", "utf-8")
        )

        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASS,
            start_tls=True,
        )
        logger.info("Contact notification sent for %s", submission_id)
        return True
    except Exception as e:
        # Log the exception type only. An SMTP failure object can carry the
        # server dialogue, and the same convention already applies elsewhere
        # in this codebase for anything near credentials.
        logger.error(
            "Failed to send contact notification for %s: %s",
            submission_id,
            type(e).__name__,
        )
        return False
