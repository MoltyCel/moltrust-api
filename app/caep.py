"""MolTrust CAEP Profile v1 — Continuous Access Evaluation events.

Proprietary event protocol, name-inspired by OpenID-CAEP. NOT a SET/RFC 8417
implementation.

Event types: trust_score_change, flag_added, flag_removed, did_revoked.
Soft-ack model with 90d retention before hard-delete.
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from slowapi import Limiter

from app.registry_keys import get_public_jwk

logger = logging.getLogger("caep")

DEFAULT_TTL_HOURS = 24
SCORE_CHANGE_THRESHOLD = 10.0
PENDING_LIMIT_MAX = 200

router = APIRouter(tags=["caep"])


# ── Service: emit / query / ack ─────────────────────────────────────────────

async def emit_caep_event(
    conn: asyncpg.Connection,
    did: str,
    event_type: str,
    payload: dict,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> str:
    """Emit a CAEP event. Returns the generated event_id."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    row = await conn.fetchrow(
        """
        INSERT INTO caep_events (did, event_type, payload, expires_at)
        VALUES ($1, $2, $3::jsonb, $4)
        RETURNING event_id
        """,
        did, event_type, json.dumps(payload), expires_at,
    )
    return row["event_id"]


async def get_pending_events(
    conn: asyncpg.Connection,
    did: str,
    limit: int = 50,
    since_event_id: Optional[str] = None,
) -> tuple[list[dict], bool]:
    """Return (events, has_more) for pending events for a DID."""
    if limit > PENDING_LIMIT_MAX:
        limit = PENDING_LIMIT_MAX

    if since_event_id:
        cursor_row = await conn.fetchrow(
            "SELECT created_at FROM caep_events WHERE event_id = $1",
            since_event_id,
        )
        cursor_ts = cursor_row["created_at"] if cursor_row else datetime.min.replace(tzinfo=timezone.utc)
        rows = await conn.fetch(
            """
            SELECT event_id, event_type, payload, created_at, expires_at
            FROM caep_events
            WHERE did = $1
              AND acknowledged_at IS NULL
              AND expires_at > NOW()
              AND created_at > $2
            ORDER BY created_at ASC
            LIMIT $3
            """,
            did, cursor_ts, limit + 1,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT event_id, event_type, payload, created_at, expires_at
            FROM caep_events
            WHERE did = $1
              AND acknowledged_at IS NULL
              AND expires_at > NOW()
            ORDER BY created_at ASC
            LIMIT $2
            """,
            did, limit + 1,
        )

    has_more = len(rows) > limit
    return [dict(r) for r in rows[:limit]], has_more


async def acknowledge_event(
    conn: asyncpg.Connection, event_id: str, owner_did: str
) -> dict:
    """Mark event as acknowledged.

    Scoped to owner_did: an event may only be acknowledged by the DID it was
    raised for. Acknowledging is destructive — the event stops being delivered
    by /caep/pending — so an unscoped call let anyone suppress anyone's
    security events by guessing or observing an event_id.

    Returns {status: ok|not_found|forbidden|already_ack}.
    """
    existing = await conn.fetchrow(
        "SELECT did, acknowledged_at FROM caep_events WHERE event_id = $1",
        event_id,
    )
    if existing is None:
        return {"status": "not_found"}
    if existing["did"] != owner_did:
        return {"status": "forbidden"}
    if existing["acknowledged_at"] is not None:
        return {
            "status": "already_ack",
            "acknowledged_at": existing["acknowledged_at"].isoformat(),
        }
    now = datetime.now(timezone.utc)
    await conn.execute(
        "UPDATE caep_events SET acknowledged_at = $1 WHERE event_id = $2 AND did = $3",
        now, event_id, owner_did,
    )
    return {"status": "ok", "acknowledged_at": now.isoformat()}


async def cleanup_acknowledged_events(
    conn: asyncpg.Connection,
    retention_days: int = 90,
) -> int:
    """Hard-delete events ack'd > retention_days ago. Returns deleted count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await conn.execute(
        "DELETE FROM caep_events WHERE acknowledged_at IS NOT NULL AND acknowledged_at < $1",
        cutoff,
    )
    if isinstance(result, str) and result.startswith("DELETE "):
        return int(result.split()[1])
    return 0


# ── Routes ──────────────────────────────────────────────────────────────────

def _did_keyfunc(request: Request) -> str:
    """Per-DID rate-limit key — pulls did from path params.

    Routes without a did path param (e.g. /caep/acknowledge/{event_id}) fall
    back to the caller address, so they get their own bucket instead of all
    sharing one "anonymous" one.
    """
    did = request.path_params.get("did")
    if did:
        return did
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "anonymous"


# Local slowapi limiter for per-DID enforcement on /caep/pending.
# Independent from app.main.limiter (which is IP-based, global).
_caep_limiter = Limiter(key_func=_did_keyfunc)


def _require_api_key(x_api_key: str = Header(alias="X-API-Key")) -> str:
    """app.main.verify_api_key, imported lazily.

    main.py imports this module, so a module-level import of the real
    dependency would be circular.
    """
    from app.main import verify_api_key
    return verify_api_key(x_api_key)


@router.get("/caep/pending/{did:path}")
@_caep_limiter.limit("120/hour")
async def caep_pending(
    request: Request,
    did: str,
    limit: int = Query(default=50, ge=1, le=PENDING_LIMIT_MAX),
    since: str | None = Query(default=None, description="event_id cursor for pagination"),
):
    """Pending CAEP events for a DID. Free during Early Access. 120/h per DID."""
    from app.main import db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="DB pool not ready")

    async with db_pool.acquire() as conn:
        events, has_more = await get_pending_events(conn, did, limit=limit, since_event_id=since)

    formatted = [
        {
            "event_id": e["event_id"],
            "event_type": e["event_type"],
            "payload": e["payload"] if isinstance(e["payload"], dict) else json.loads(e["payload"]),
            "created_at": e["created_at"].isoformat(),
            "expires_at": e["expires_at"].isoformat(),
        }
        for e in events
    ]
    return {
        "did": did,
        "events": formatted,
        "count": len(formatted),
        "has_more": has_more,
    }


@router.post("/caep/acknowledge/{event_id}")
@_caep_limiter.limit("120/hour")
async def caep_acknowledge(
    request: Request,
    event_id: str,
    api_key: str = Depends(_require_api_key),
):
    """Mark a CAEP event as acknowledged. Only the DID the event was raised for."""
    from app.main import db_pool, resolve_did_from_api_key
    if db_pool is None:
        raise HTTPException(status_code=503, detail="DB pool not ready")

    async with db_pool.acquire() as conn:
        caller_did = await resolve_did_from_api_key(conn, api_key)
        if caller_did is None:
            raise HTTPException(status_code=403, detail="API key is not bound to a DID")
        result = await acknowledge_event(conn, event_id, caller_did)

    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Event not found")
    if result["status"] == "forbidden":
        raise HTTPException(status_code=403, detail="Event belongs to a different DID")
    if result["status"] == "already_ack":
        raise HTTPException(
            status_code=409,
            detail={"detail": "Event already acknowledged", "acknowledged_at": result["acknowledged_at"]},
        )
    return {
        "event_id": event_id,
        "acknowledged": True,
        "acknowledged_at": result["acknowledged_at"],
    }


@router.get("/.well-known/registry-key.json")
async def well_known_registry_key(response: Response):
    """Public Ed25519 registry key in JWK format. 1h cache."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    return get_public_jwk()
