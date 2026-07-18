"""Reseller portal (Phase 2, 2026-07-18).

Multi-tenant reseller portal. A *reseller* is a payer (accounts.payer_ref) marked
as reseller, with its own password login and an assigned wholesale price. The
reseller onboards its customer agents self-service by binding a DID to its own
payer_ref; its dashboard shows only its own agents, traffic and monthly total.

Design anchors (from the Phase-1 Ist-Stand of this repo):
  * bcrypt is the house password primitive (app/admin_auth.py) -> reused here.
  * The did<->payer link is the role-owned side table agent_payer (agents is
    postgres-owned). agent_payer.did is PRIMARY KEY, so a DID can already only
    map to ONE payer globally. Onboarding reuses that table and turns the silent
    ON CONFLICT DO NOTHING into an explicit 409 (no leak of the current owner).
  * Sessions are DB-backed (reseller_sessions), token hashed at rest — unlike the
    in-memory admin SESSIONS dict, these survive restart and span workers.
  * The invoice is seat-based: N agents x wholesale_price (EUR). It does NOT
    depend on per-call metering. Stripe invoicing runs against a SEPARATE
    test-mode key and never touches the live key; live faktura is flag-gated off.

Tenant isolation is the load-bearing property: EVERY query is filtered by the
payer_ref resolved from the caller's session token — never global.
"""
import os
import re
import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta

import bcrypt
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from app.admin_rbac import verify_admin, AdminPermission

log = logging.getLogger("reseller")

SESSION_TTL_HOURS = int(os.getenv("RESELLER_SESSION_TTL_HOURS", "24"))
# DID accepted for onboarding. Resellers may bring external agents, so this is
# the generic DID shape, not the moltrust-only pattern. Bounded length keeps it
# index-friendly and rejects junk.
_DID_RE = re.compile(r"^did:[a-z0-9]+:[A-Za-z0-9._%:-]{1,180}$")

# Precomputed hash used to keep failed-login timing roughly uniform (a real
# bcrypt verify is spent whether or not the login exists). checkpw needs a full
# 60-char hash, not a bare salt.
_DUMMY_HASH = bcrypt.hashpw(b"timing-uniform", bcrypt.gensalt())


def _pool():
    # Resolve the app pool at call-time (None until app.main lifespan runs).
    import app.main as _m
    return _m.db_pool


def _norm_login(login: str) -> str:
    return (login or "").strip().lower()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _valid_did(did: str) -> bool:
    return bool(did) and len(did) <= 200 and bool(_DID_RE.match(did))


# ---------------------------------------------------------------------------
# Schema (mirrors app/migrations/2026-07-18_reseller_portal.sql up)
# ---------------------------------------------------------------------------
async def ensure_reseller_tables(conn):
    """Idempotent schema. Mirrors the reseller_portal migration (up)."""
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reseller_accounts (
            payer_ref             TEXT PRIMARY KEY REFERENCES accounts(payer_ref),
            login                 TEXT UNIQUE NOT NULL,
            password_hash         TEXT NOT NULL,
            display_name          TEXT,
            wholesale_price_cents INTEGER NOT NULL CHECK (wholesale_price_cents >= 0),
            currency              TEXT NOT NULL DEFAULT 'EUR' CHECK (currency = 'EUR'),
            active                BOOLEAN NOT NULL DEFAULT true,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reseller_sessions (
            token_sha256 TEXT PRIMARY KEY,
            payer_ref    TEXT NOT NULL REFERENCES reseller_accounts(payer_ref),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at   TIMESTAMPTZ NOT NULL,
            revoked      BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_sessions_payer ON reseller_sessions(payer_ref)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_sessions_expires ON reseller_sessions(expires_at)")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reseller_assignment_audit (
            id        BIGSERIAL PRIMARY KEY,
            ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
            payer_ref TEXT NOT NULL,
            did       TEXT NOT NULL,
            action    TEXT NOT NULL,
            actor     TEXT,
            detail    JSONB,
            row_hash  TEXT
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_audit_payer ON reseller_assignment_audit(payer_ref)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_audit_did ON reseller_assignment_audit(did)")
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION reseller_audit_bind_hash() RETURNS trigger AS $$
        BEGIN
          NEW.row_hash := 'sha256:' || encode(
            digest(
              coalesce(to_char(NEW.ts, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), '') || '|' ||
              NEW.payer_ref || '|' || NEW.did || '|' || NEW.action || '|' ||
              coalesce(NEW.actor, ''),
              'sha256'),
            'hex');
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    await conn.execute("DROP TRIGGER IF EXISTS trg_reseller_audit_bind ON reseller_assignment_audit")
    await conn.execute(
        "CREATE TRIGGER trg_reseller_audit_bind BEFORE INSERT ON reseller_assignment_audit "
        "FOR EACH ROW EXECUTE FUNCTION reseller_audit_bind_hash()"
    )
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION reseller_audit_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'reseller_assignment_audit is append-only: % forbidden', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    await conn.execute("DROP TRIGGER IF EXISTS trg_reseller_audit_immutable ON reseller_assignment_audit")
    await conn.execute(
        "CREATE TRIGGER trg_reseller_audit_immutable BEFORE UPDATE OR DELETE ON reseller_assignment_audit "
        "FOR EACH ROW EXECUTE FUNCTION reseller_audit_immutable()"
    )


# ---------------------------------------------------------------------------
# Reseller lifecycle (manual anlage by us — never self-service)
# ---------------------------------------------------------------------------
async def create_reseller(conn, login, password, wholesale_price_cents,
                          display_name=None, email=None, payer_ref=None):
    """Create (or attach) a reseller. Mints an accounts row if payer_ref is new.

    Returns the payer_ref. Raises ValueError on bad input / duplicate login.
    Password is bcrypt-hashed; the plaintext is never stored or logged.
    """
    from app.accounts import new_payer_ref
    login = _norm_login(login)
    if not login or not password:
        raise ValueError("login and password are required")
    if not isinstance(wholesale_price_cents, int) or wholesale_price_cents < 0:
        raise ValueError("wholesale_price_cents must be a non-negative integer (EUR minor units)")

    if payer_ref is None:
        payer_ref = new_payer_ref()
        await conn.execute(
            "INSERT INTO accounts (payer_ref, email) VALUES ($1, $2) ON CONFLICT (payer_ref) DO NOTHING",
            payer_ref, email,
        )
    else:
        exists = await conn.fetchval("SELECT 1 FROM accounts WHERE payer_ref = $1", payer_ref)
        if not exists:
            await conn.execute(
                "INSERT INTO accounts (payer_ref, email) VALUES ($1, $2) ON CONFLICT (payer_ref) DO NOTHING",
                payer_ref, email,
            )

    dup = await conn.fetchval("SELECT payer_ref FROM reseller_accounts WHERE login = $1", login)
    if dup:
        raise ValueError("login already exists")

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    await conn.execute(
        "INSERT INTO reseller_accounts "
        "(payer_ref, login, password_hash, display_name, wholesale_price_cents) "
        "VALUES ($1, $2, $3, $4, $5)",
        payer_ref, login, pw_hash, display_name, wholesale_price_cents,
    )
    log.info("reseller created payer_ref=%s login=%s", payer_ref, login)
    return payer_ref


# ---------------------------------------------------------------------------
# Auth: login / session / logout
# ---------------------------------------------------------------------------
async def _authenticate(conn, login, password):
    """Return payer_ref on success, else None. Generic on failure (no user enum)."""
    row = await conn.fetchrow(
        "SELECT payer_ref, password_hash, active FROM reseller_accounts WHERE login = $1",
        _norm_login(login),
    )
    if not row or not row["active"]:
        # Spend a hash to keep timing roughly uniform whether or not the user exists.
        bcrypt.checkpw(b"timing-uniform-x", _DUMMY_HASH)
        return None
    try:
        if bcrypt.checkpw((password or "").encode(), row["password_hash"].encode()):
            return row["payer_ref"]
    except ValueError:
        return None
    return None


async def _issue_session(conn, payer_ref):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    await conn.execute(
        "INSERT INTO reseller_sessions (token_sha256, payer_ref, expires_at) VALUES ($1, $2, $3)",
        _hash_token(token), payer_ref, expires,
    )
    return token, expires


async def _resolve_session(conn, token):
    if not token:
        return None
    row = await conn.fetchrow(
        "SELECT payer_ref, expires_at, revoked FROM reseller_sessions WHERE token_sha256 = $1",
        _hash_token(token),
    )
    if not row or row["revoked"]:
        return None
    if datetime.now(timezone.utc) > row["expires_at"]:
        return None
    return row["payer_ref"]


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


async def require_reseller(request: Request) -> str:
    """FastAPI dependency: resolve the caller's payer_ref or 401. This single
    choke point is what makes every downstream query tenant-scoped."""
    token = _bearer(request)
    async with _pool().acquire() as conn:
        payer_ref = await _resolve_session(conn, token)
    if not payer_ref:
        raise HTTPException(401, "reseller authentication required")
    return payer_ref


# ---------------------------------------------------------------------------
# Onboarding + billing queries (ALWAYS scoped to the passed payer_ref)
# ---------------------------------------------------------------------------
async def onboard_agent(conn, payer_ref, did, actor=None):
    """Bind a DID to this reseller's payer_ref. Reuses agent_payer (global unique).

    Returns ('created'|'exists'). Raises HTTPException(409) if the DID belongs to
    ANOTHER payer — without revealing who. Every attempt is audited.
    """
    if not _valid_did(did):
        raise HTTPException(400, "invalid DID format")
    current = await conn.fetchval("SELECT payer_ref FROM agent_payer WHERE did = $1", did)
    if current == payer_ref:
        return "exists"
    if current is not None:
        await conn.execute(
            "INSERT INTO reseller_assignment_audit (payer_ref, did, action, actor) VALUES ($1, $2, 'rejected_conflict', $3)",
            payer_ref, did, actor,
        )
        # No leak of the current owner.
        raise HTTPException(409, "DID is already assigned")
    await conn.execute(
        "INSERT INTO agent_payer (did, payer_ref) VALUES ($1, $2) ON CONFLICT (did) DO NOTHING",
        did, payer_ref,
    )
    # Guard against a race: re-read and confirm we own it.
    owner = await conn.fetchval("SELECT payer_ref FROM agent_payer WHERE did = $1", did)
    if owner != payer_ref:
        await conn.execute(
            "INSERT INTO reseller_assignment_audit (payer_ref, did, action, actor) VALUES ($1, $2, 'rejected_conflict', $3)",
            payer_ref, did, actor,
        )
        raise HTTPException(409, "DID is already assigned")
    await conn.execute(
        "INSERT INTO reseller_assignment_audit (payer_ref, did, action, actor) VALUES ($1, $2, 'assigned', $3)",
        payer_ref, did, actor,
    )
    return "created"


async def list_agents(conn, payer_ref):
    """Agents of THIS reseller, with cumulative metered-call traffic (0 if none).

    Traffic is the lifetime payer_usage_meter counter (paid-bypass calls only) —
    honest column, not a per-month figure; real per-period metering is a later
    feature per the Phase-1 decision.
    """
    rows = await conn.fetch(
        """
        SELECT ap.did,
               ap.created_at,
               COALESCE(m.calls, 0)      AS calls,
               COALESCE(m.metered_cost, 0) AS metered_cost,
               m.last_call
        FROM agent_payer ap
        LEFT JOIN payer_usage_meter m
          ON m.did = ap.did AND m.payer_ref = ap.payer_ref
        WHERE ap.payer_ref = $1
        ORDER BY ap.created_at
        """,
        payer_ref,
    )
    return [
        {
            "did": r["did"],
            "onboarded_at": r["created_at"].isoformat() if r["created_at"] else None,
            "calls": int(r["calls"]),
            "metered_cost": int(r["metered_cost"]),
            "last_call": r["last_call"].isoformat() if r["last_call"] else None,
        }
        for r in rows
    ]


async def billing_summary(conn, payer_ref):
    """Seat-based monthly total for THIS reseller: N agents x wholesale price."""
    acct = await conn.fetchrow(
        "SELECT login, display_name, wholesale_price_cents, currency FROM reseller_accounts WHERE payer_ref = $1",
        payer_ref,
    )
    if not acct:
        raise HTTPException(404, "reseller not found")
    agents = await list_agents(conn, payer_ref)
    count = len(agents)
    price = int(acct["wholesale_price_cents"])
    return {
        "reseller": acct["display_name"] or acct["login"],
        "currency": acct["currency"],
        "wholesale_price_cents": price,
        "agent_count": count,
        "month_total_cents": count * price,
        "agents": agents,
    }


# ---------------------------------------------------------------------------
# HTTP router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/reseller", tags=["reseller"])
admin_router = APIRouter(prefix="/admin/reseller", tags=["admin-reseller"])


class LoginBody(BaseModel):
    login: str
    password: str


class OnboardBody(BaseModel):
    did: str


class CreateResellerBody(BaseModel):
    login: str
    password: str
    wholesale_price_cents: int
    display_name: str | None = None
    email: str | None = None


# Best-effort in-process login throttle, keyed on (ip, login) — brute force
# targets a specific login, and per-login keying keeps one NAT'd IP (a reseller
# office) from locking itself out across accounts. The real protection is bcrypt
# cost + generic errors; this just blunts online guessing. slowapi (used for
# /admin/login) can be layered on at the app level later.
_LOGIN_HITS: dict[str, list[float]] = {}


def _throttle_ok(key: str, now: float, limit: int = 8, window: float = 60.0) -> bool:
    hits = [t for t in _LOGIN_HITS.get(key, []) if now - t < window]
    hits.append(now)
    _LOGIN_HITS[key] = hits
    return len(hits) <= limit


@router.post("/login")
async def reseller_login(body: LoginBody, request: Request):
    import time
    ip = request.client.host if request.client else "unknown"
    if not _throttle_ok(f"{ip}|{_norm_login(body.login)}", time.time()):
        raise HTTPException(429, "too many attempts, slow down")
    async with _pool().acquire() as conn:
        payer_ref = await _authenticate(conn, body.login, body.password)
        if not payer_ref:
            raise HTTPException(401, "invalid credentials")
        token, expires = await _issue_session(conn, payer_ref)
    return {"token": token, "expires_at": expires.isoformat()}


@router.get("/me")
async def reseller_me(payer_ref: str = Depends(require_reseller)):
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT login, display_name, wholesale_price_cents, currency FROM reseller_accounts WHERE payer_ref = $1",
            payer_ref,
        )
    if not row:
        raise HTTPException(404, "reseller not found")
    return {
        "login": row["login"],
        "display_name": row["display_name"],
        "wholesale_price_cents": int(row["wholesale_price_cents"]),
        "currency": row["currency"],
    }


@router.post("/logout")
async def reseller_logout(request: Request):
    token = _bearer(request)
    if token:
        async with _pool().acquire() as conn:
            await conn.execute(
                "UPDATE reseller_sessions SET revoked = true WHERE token_sha256 = $1",
                _hash_token(token),
            )
    return {"ok": True}


@router.get("/agents")
async def reseller_agents(payer_ref: str = Depends(require_reseller)):
    async with _pool().acquire() as conn:
        return {"agents": await list_agents(conn, payer_ref)}


@router.post("/agents")
async def reseller_onboard(body: OnboardBody, request: Request,
                           payer_ref: str = Depends(require_reseller)):
    async with _pool().acquire() as conn:
        actor = await conn.fetchval("SELECT login FROM reseller_accounts WHERE payer_ref = $1", payer_ref)
        result = await onboard_agent(conn, payer_ref, body.did.strip(), actor=actor)
    return {"did": body.did.strip(), "status": result}


@router.get("/billing")
async def reseller_billing(payer_ref: str = Depends(require_reseller)):
    async with _pool().acquire() as conn:
        return await billing_summary(conn, payer_ref)


@admin_router.post("")
async def admin_create_reseller(body: CreateResellerBody, request: Request):
    """Manual reseller anlage by us (X-Admin-Key, WRITE). Never self-service."""
    verify_admin(request, AdminPermission.WRITE)
    async with _pool().acquire() as conn:
        try:
            payer_ref = await create_reseller(
                conn, body.login, body.password, body.wholesale_price_cents,
                display_name=body.display_name, email=body.email,
            )
        except ValueError as e:
            raise HTTPException(409, str(e))
    return {"payer_ref": payer_ref, "login": _norm_login(body.login)}
