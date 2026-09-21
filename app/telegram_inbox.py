"""Inbound Telegram updates, stored for whoever wants them.

`getUpdates` is exclusive per bot token. Two pollers on one token steal each
other's updates, which is why ThreadWatch owned the token alone and the reply
radar could not have an approval loop at all. A webhook has no such limit: the
route writes every update here and each consumer claims what it recognises.

The endpoint does as little as possible — verify the shared secret, insert,
return 200. Telegram retries anything that is not a fast 200, so parsing,
routing and business logic belong to the consumers, not to the request.

Delivery is at-least-once: `update_id` is the primary key and the insert is
`ON CONFLICT DO NOTHING`, so a retried update is stored once.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("moltrust")

SECRET_ENV = "TELEGRAM_WEBHOOK_SECRET"
SECRETS_FILE = os.path.expanduser("~/.moltrust_secrets")

# Telegram sends everything by default; we want the two kinds of update that
# carry a human decision. Anything else is noise we would store and never read.
ALLOWED_UPDATES = ["message", "callback_query"]

# A single update is small. This is the ceiling before we stop believing it.
MAX_PAYLOAD_BYTES = 262_144


def webhook_secret() -> str:
    """The shared secret, from the environment or the secrets file.

    Same fallback as app/notify.py: the standalone scripts load the secrets
    file into a dict of their own and never touch os.environ.
    """
    value = os.environ.get(SECRET_ENV, "").strip()
    if value:
        return value
    try:
        with open(os.environ.get("MOLTRUST_SECRETS_FILE", SECRETS_FILE)) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith(SECRET_ENV + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


async def ensure_telegram_inbox_tables(conn) -> None:
    """Create `telegram_inbox` if it is missing.

    Same DDL as migrations/2026-09-21_telegram_inbox.sql. Additive, idempotent,
    and created fresh by the `moltstack` role.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_inbox (
            update_id    BIGINT      PRIMARY KEY,
            payload      JSONB       NOT NULL,
            consumed_by  TEXT,
            consumed_at  TIMESTAMPTZ,
            ts           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_telegram_inbox_unconsumed
            ON telegram_inbox (ts) WHERE consumed_by IS NULL
        """
    )


async def store_update(conn, update: dict) -> bool:
    """Insert one update. False when it was already there (a Telegram retry)."""
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        raise ValueError("update without an integer update_id")
    row = await conn.fetchrow(
        """INSERT INTO telegram_inbox (update_id, payload)
                VALUES ($1, $2::jsonb)
           ON CONFLICT (update_id) DO NOTHING
             RETURNING update_id""",
        update_id, json.dumps(update),
    )
    return row is not None


def kind(update: dict) -> str:
    """Which of the allowed shapes this update is, or 'other'."""
    for name in ALLOWED_UPDATES:
        if name in update:
            return name
    return "other"


# ── Sync side, for the standalone consumers ──
#
# The scripts that read this table are plain synchronous programs; the API is
# async. Both shapes live here so the claim semantics are written once.

DB_DSN = os.environ.get("DATABASE_URL", "dbname=moltstack user=moltstack")


def claim(consumer: str, kinds: list[str], limit: int = 100) -> list[dict]:
    """Take up to `limit` unclaimed updates of the given kinds.

    A claim is a single UPDATE … RETURNING with `FOR UPDATE SKIP LOCKED`, so
    two consumers running at the same moment never hand each other the same
    row. Each consumer names the kinds it understands: ThreadWatch takes
    `message`, the reply radar takes `callback_query`, and neither eats the
    other's post.

    Claiming marks the row consumed whether or not the consumer then succeeds.
    That is deliberate — a poison update that crashes its reader must not be
    re-served forever. The payload stays in the table to be looked at.
    """
    if not kinds:
        return []
    import psycopg2

    # `?|` is jsonb "has any of these keys" and takes a text[], so the
    # statement is a constant however many kinds a consumer asks for. Building
    # one OR-clause per kind would have been dynamic SQL for no gain.
    sql = """
        UPDATE telegram_inbox SET consumed_by = %s, consumed_at = now()
         WHERE update_id IN (
               SELECT update_id FROM telegram_inbox
                WHERE consumed_by IS NULL AND payload ?| %s
                ORDER BY ts
                LIMIT %s
                FOR UPDATE SKIP LOCKED)
     RETURNING payload
    """
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (consumer, list(kinds), limit))
            rows = cur.fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def pending(kinds: list[str] | None = None) -> int:
    """How many unclaimed updates are waiting. For health checks."""
    import psycopg2
    conn = psycopg2.connect(DB_DSN)
    try:
        with conn, conn.cursor() as cur:
            if kinds:
                cur.execute("SELECT count(*) FROM telegram_inbox "
                            "WHERE consumed_by IS NULL AND payload ?| %s", (list(kinds),))
            else:
                cur.execute("SELECT count(*) FROM telegram_inbox WHERE consumed_by IS NULL")
            return int(cur.fetchone()[0])
    finally:
        conn.close()
