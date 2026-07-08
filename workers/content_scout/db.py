"""Postgres access for the content_review_queue (asyncpg)."""
import json

import asyncpg

from . import config


async def connect(secrets: dict):
    return await asyncpg.connect(host=config.DB_HOST, database=config.DB_NAME,
                                 user=config.DB_USER, password=secrets.get("MOLTSTACK_DB_PW", ""))


async def seen_refs(conn) -> set:
    rows = await conn.fetch("SELECT source_ref FROM content_review_queue")
    return {r["source_ref"] for r in rows}


async def insert_row(conn, row: dict) -> bool:
    """Idempotent insert; ON CONFLICT (source_ref) DO NOTHING. Returns True if new."""
    res = await conn.execute("""
        INSERT INTO content_review_queue
          (source, source_ref, classification, class_reason, draft_type, target,
           draft_md, verify_status, model_used, tokens_in, tokens_out, cost_est, state)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,'pending_review')
        ON CONFLICT (source_ref) DO NOTHING
    """, row["source"], row["source_ref"], row["classification"], row.get("class_reason"),
        row.get("draft_type", "none"), row.get("target"), row.get("draft_md"),
        json.dumps(row.get("verify_status", [])), row.get("model_used"),
        row.get("tokens_in", 0), row.get("tokens_out", 0), row.get("cost_est", 0))
    return res.endswith("1")


# --- CLI helpers ---
async def list_pending(conn):
    return await conn.fetch("""
        SELECT id, source, classification, draft_type, target, cost_est, created_at
        FROM content_review_queue WHERE state='pending_review'
        ORDER BY created_at DESC""")


async def get_row(conn, rid: int):
    return await conn.fetchrow("SELECT * FROM content_review_queue WHERE id=$1", rid)


async def set_state(conn, rid: int, state: str) -> bool:
    res = await conn.execute(
        "UPDATE content_review_queue SET state=$2, reviewed_at=now() WHERE id=$1", rid, state)
    return res.endswith("1")
