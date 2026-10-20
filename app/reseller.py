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
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException, Depends
from pydantic import BaseModel

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
            customer_vat_id       TEXT,
            active                BOOLEAN NOT NULL DEFAULT true,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Additive for already-created installs (recipient USt-IdNr for reverse-charge invoicing).
    await conn.execute("ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS customer_vat_id TEXT")
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
    await ensure_reseller_suspend_schema(conn)
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


async def ensure_reseller_suspend_schema(conn):
    """Idempotent. Mirrors app/migrations/2026-10-16_reseller_suspend.sql (up)."""
    await conn.execute(
        "ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'"
    )
    await conn.execute("ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ")
    await conn.execute("ALTER TABLE reseller_accounts ADD COLUMN IF NOT EXISTS suspend_reason TEXT")
    await conn.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reseller_accounts_status_chk') THEN
            ALTER TABLE reseller_accounts
              ADD CONSTRAINT reseller_accounts_status_chk CHECK (status IN ('active', 'suspended'));
          END IF;
        END $$
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reseller_applications (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ts                   TIMESTAMPTZ NOT NULL DEFAULT now(),
            company              TEXT NOT NULL CHECK (length(company) BETWEEN 2 AND 200),
            contact_name         TEXT NOT NULL CHECK (length(contact_name) BETWEEN 2 AND 120),
            email                TEXT NOT NULL CHECK (length(email) <= 254),
            product_description  TEXT NOT NULL CHECK (length(product_description) BETWEEN 20 AND 4000),
            expected_agent_count INTEGER NOT NULL CHECK (expected_agent_count BETWEEN 1 AND 1000000),
            ip_hash              TEXT,
            status               TEXT NOT NULL DEFAULT 'new'
                                 CHECK (status IN ('new', 'reviewed', 'accepted', 'rejected')),
            notified             BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reseller_applications_ts ON reseller_applications (ts DESC)"
    )


# ---------------------------------------------------------------------------
# Reseller lifecycle (manual anlage by us — never self-service)
# ---------------------------------------------------------------------------
async def create_reseller(conn, login, password, wholesale_price_cents,
                          display_name=None, email=None, payer_ref=None, vat_id=None):
    """Create (or attach) a reseller. Mints an accounts row if payer_ref is new.

    Returns the payer_ref. Raises ValueError on bad input / duplicate login.
    Password is bcrypt-hashed; the plaintext is never stored or logged. vat_id is
    the recipient USt-IdNr (may be set later via set_reseller_vat_id).
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
        "(payer_ref, login, password_hash, display_name, wholesale_price_cents, customer_vat_id) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        payer_ref, login, pw_hash, display_name, wholesale_price_cents,
        (vat_id or "").strip() or None,
    )
    log.info("reseller created payer_ref=%s login=%s", payer_ref, login)
    return payer_ref


async def set_reseller_vat_id(conn, payer_ref, vat_id):
    """Set/clear the recipient USt-IdNr for a reseller."""
    await conn.execute(
        "UPDATE reseller_accounts SET customer_vat_id = $1 WHERE payer_ref = $2",
        (vat_id or "").strip() or None, payer_ref,
    )


# ---------------------------------------------------------------------------
# Suspension (ops job RES-SUSP) — state change only, never a deletion
# ---------------------------------------------------------------------------
MAX_REASON_LEN = 500
_REASON_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ResellerNotFound(LookupError):
    pass


class ResellerStateConflict(ValueError):
    pass


def clean_reason(reason) -> str:
    """Mandatory, single-line, bounded. Raises ValueError otherwise."""
    if not isinstance(reason, str):
        raise ValueError("reason is required")
    r = _REASON_CONTROL_RE.sub(" ", reason).strip()
    if not r:
        raise ValueError("reason is required")
    if len(r) > MAX_REASON_LEN:
        raise ValueError(f"reason must be at most {MAX_REASON_LEN} characters")
    return r


async def set_reseller_status(conn, payer_ref, new_status, reason, actor):
    """Suspend or reactivate a reseller. Returns a summary dict.

    Touches ONLY reseller_accounts.status/suspended_at/suspend_reason and, on
    suspend, flips reseller_sessions.revoked to true for open sessions (UPDATE,
    not DELETE). agent_payer, agents, both audit tables and master data are not
    modified. Writes one reseller_admin_audit row (action 'suspend' or
    'reactivate', detail {"reason": ..., "revoked_sessions": n}) in the same
    transaction as the state change, so there is no state change without audit.
    """
    import json
    if new_status not in ("active", "suspended"):
        raise ValueError("status must be 'active' or 'suspended'")
    reason = clean_reason(reason)
    actor = (actor or "").strip()
    if not actor:
        raise ValueError("actor is required")
    action = "suspend" if new_status == "suspended" else "reactivate"
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT status FROM reseller_accounts WHERE payer_ref = $1 FOR UPDATE", payer_ref
        )
        if not row:
            raise ResellerNotFound("reseller not found")
        if row["status"] == new_status:
            raise ResellerStateConflict(f"reseller is already {new_status}")
        if new_status == "suspended":
            await conn.execute(
                "UPDATE reseller_accounts SET status = 'suspended', suspended_at = now(), "
                "suspend_reason = $2 WHERE payer_ref = $1",
                payer_ref, reason,
            )
            revoked = await conn.fetchval(
                "WITH r AS (UPDATE reseller_sessions SET revoked = true "
                "WHERE payer_ref = $1 AND NOT revoked RETURNING 1) SELECT count(*) FROM r",
                payer_ref,
            )
        else:
            await conn.execute(
                "UPDATE reseller_accounts SET status = 'active', suspended_at = NULL, "
                "suspend_reason = NULL WHERE payer_ref = $1",
                payer_ref,
            )
            revoked = 0
        detail = {"reason": reason, "revoked_sessions": int(revoked)}
        await conn.execute(
            "INSERT INTO reseller_admin_audit (actor, action, target_payer_ref, detail) "
            "VALUES ($1, $2, $3, $4)",
            actor, action, payer_ref, json.dumps(detail),
        )
    log.info("reseller %s payer_ref=%s actor=%s revoked_sessions=%s",
             action, payer_ref, actor, revoked)
    return {"payer_ref": payer_ref, "status": new_status, "action": action,
            "revoked_sessions": int(revoked)}


# ---------------------------------------------------------------------------
# Auth: login / session / logout
# ---------------------------------------------------------------------------
class ResellerSuspended(Exception):
    """The caller proved ownership (password or live session) but the reseller is
    suspended. Mapped to HTTP 403. Raised ONLY after the credential is verified,
    so the suspended state is never revealed to someone who does not hold it."""


async def _authenticate(conn, login, password):
    """Return payer_ref on success, else None. Generic on failure (no user enum).

    Raises ResellerSuspended only when the password is correct and the reseller
    is suspended. Unknown login, wrong password and active=false all stay the
    same generic None, with one bcrypt verification spent on every path.
    """
    row = await conn.fetchrow(
        "SELECT payer_ref, password_hash, active, status FROM reseller_accounts WHERE login = $1",
        _norm_login(login),
    )
    if not row or not row["active"]:
        # Spend a hash to keep timing roughly uniform whether or not the user exists.
        bcrypt.checkpw(b"timing-uniform-x", _DUMMY_HASH)
        return None
    try:
        ok = bcrypt.checkpw((password or "").encode(), row["password_hash"].encode())
    except ValueError:
        return None
    if not ok:
        return None
    if row["status"] != "active":
        raise ResellerSuspended()
    return row["payer_ref"]


async def _issue_session(conn, payer_ref):
    # Hex, not token_urlsafe: the response scrubber in app/main.py
    # (SENSITIVE_PATTERNS) redacts any run of exactly 40 base64 characters and
    # "sk-" followed by 20 alphanumerics. About 1.8 % of token_urlsafe(32)
    # values contain one, and the caller then received a token with
    # "[REDACTED]" in the middle that matched no session. Hex cannot match
    # either pattern; the entropy is the same 256 bits.
    token = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    await conn.execute(
        "INSERT INTO reseller_sessions (token_sha256, payer_ref, expires_at) VALUES ($1, $2, $3)",
        _hash_token(token), payer_ref, expires,
    )
    return token, expires


async def _resolve_session(conn, token):
    if not token:
        return None
    # The account state is re-checked on EVERY request, not only at login: a
    # session issued before a suspension (or before active=false) must stop
    # working immediately, even if the revoke in set_reseller_status raced it.
    row = await conn.fetchrow(
        "SELECT s.payer_ref, s.expires_at, s.revoked, r.active, r.status "
        "FROM reseller_sessions s JOIN reseller_accounts r ON r.payer_ref = s.payer_ref "
        "WHERE s.token_sha256 = $1",
        _hash_token(token),
    )
    if not row or row["revoked"] or not row["active"]:
        return None
    if datetime.now(timezone.utc) > row["expires_at"]:
        return None
    if row["status"] != "active":
        raise ResellerSuspended()
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
    try:
        async with _pool().acquire() as conn:
            payer_ref = await _resolve_session(conn, token)
    except ResellerSuspended:
        raise HTTPException(403, "reseller account suspended")
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
               m.last_call,
               EXISTS(SELECT 1 FROM agents a WHERE a.did = ap.did) AS registered
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
            # Resolved at query time against `agents` — never persisted, so it
            # flips to active automatically once the DID registers.
            "status": "active" if r["registered"] else "pending",
        }
        for r in rows
    ]


async def billing_summary(conn, payer_ref):
    """Seat-based monthly total for THIS reseller: N agents x wholesale price."""
    acct = await conn.fetchrow(
        "SELECT login, display_name, wholesale_price_cents, currency, customer_vat_id "
        "FROM reseller_accounts WHERE payer_ref = $1",
        payer_ref,
    )
    if not acct:
        raise HTTPException(404, "reseller not found")
    agents = await list_agents(conn, payer_ref)
    active_count = sum(1 for a in agents if a["status"] == "active")
    pending_count = len(agents) - active_count
    price = int(acct["wholesale_price_cents"])
    # Billing base is active agents only: a pending assignment (DID not yet in
    # `agents`) NEVER enters the monthly total.
    return {
        "reseller": acct["display_name"] or acct["login"],
        "currency": acct["currency"],
        "wholesale_price_cents": price,
        "active_count": active_count,
        "pending_count": pending_count,
        "month_total_cents": active_count * price,
        "customer_vat_id": acct["customer_vat_id"],
        "agents": agents,
    }


# ---------------------------------------------------------------------------
# HTTP router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/reseller", tags=["reseller"])


class LoginBody(BaseModel):
    login: str
    password: str


class OnboardBody(BaseModel):
    did: str


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
        try:
            payer_ref = await _authenticate(conn, body.login, body.password)
        except ResellerSuspended:
            # Correct password, suspended account: tell the owner, issue nothing.
            raise HTTPException(403, "reseller account suspended")
        if not payer_ref:
            raise HTTPException(401, "invalid credentials")
        token, expires = await _issue_session(conn, payer_ref)
    return {"token": token, "expires_at": expires.isoformat()}


@router.get("/me")
async def reseller_me(payer_ref: str = Depends(require_reseller)):
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT login, display_name, wholesale_price_cents, currency, customer_vat_id "
            "FROM reseller_accounts WHERE payer_ref = $1",
            payer_ref,
        )
    if not row:
        raise HTTPException(404, "reseller not found")
    return {
        "login": row["login"],
        "display_name": row["display_name"],
        "wholesale_price_cents": int(row["wholesale_price_cents"]),
        "currency": row["currency"],
        "customer_vat_id": row["customer_vat_id"],
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


# ---------------------------------------------------------------------------
# Reseller applications (POST /reseller/apply) — intake only, no account
# ---------------------------------------------------------------------------
# An application is a row in reseller_applications plus a Telegram notice to
# the operator. It NEVER creates an accounts or reseller_accounts row: becoming
# a reseller stays a manual step (scripts/create_reseller.py / admin tab).
#
# Rate limit: a local slowapi limiter (precedent: app/caep.py), keyed on the
# CIDR-gated client IP resolver from app.main, imported lazily because main
# imports this module. Default 3/hour per IP, overridable by env.
from pydantic import Field, field_validator  # noqa: E402
from slowapi import Limiter  # noqa: E402

RESELLER_APPLY_RATE_LIMIT = os.getenv("RESELLER_APPLY_RATE_LIMIT", "3/hour")
APPLY_ACCEPTED_RESPONSE = {
    "status": "received",
    "message": "Thank you. We review every application and will get back to you by email.",
}
_APPLY_MAX = {"company": 200, "contact_name": 120, "email": 254,
              "product_description": 4000, "website": 256}
_APPLY_MIN = {"company": 2, "contact_name": 2, "product_description": 20}
_APPLY_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_APPLY_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MAX_EXPECTED_AGENTS = 1_000_000


def _apply_client_ip(request: Request) -> str:
    from app.main import _get_client_ip
    return _get_client_ip(request)


_apply_limiter = Limiter(key_func=_apply_client_ip)


class ApplyBody(BaseModel):
    company: str = Field(max_length=_APPLY_MAX["company"])
    contact_name: str = Field(max_length=_APPLY_MAX["contact_name"])
    email: str = Field(max_length=_APPLY_MAX["email"])
    product_description: str = Field(max_length=_APPLY_MAX["product_description"])
    expected_agent_count: int = Field(ge=1, le=MAX_EXPECTED_AGENTS, strict=True)
    website: str = Field(default="", max_length=_APPLY_MAX["website"])  # honeypot

    @field_validator("company", "contact_name", "email", "product_description", "website", mode="before")
    @classmethod
    def _clean(cls, v):
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ValueError("must be a string")
        return _APPLY_CONTROL_RE.sub("", v).strip()

    @field_validator("company", "contact_name", "email")
    @classmethod
    def _single_line(cls, v: str) -> str:
        if "\n" in v or "\r" in v or "\t" in v:
            raise ValueError("must be a single line")
        return v

    @field_validator("company", "contact_name", "product_description")
    @classmethod
    def _min_len(cls, v: str, info) -> str:
        if len(v) < _APPLY_MIN[info.field_name]:
            raise ValueError(f"must be at least {_APPLY_MIN[info.field_name]} characters")
        return v

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        if not _APPLY_EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v


def _ip_hash(request: Request) -> str:
    """sha256 over the /24 (IPv4) or /64 (IPv6) anonymized source address. The
    raw IP is never stored; the hash is only good for spotting repeat senders."""
    from app.main import _anonymize_ip
    anon = _anonymize_ip(_apply_client_ip(request)) or "unknown"
    return "sha256:" + hashlib.sha256(f"reseller-apply|{anon}".encode()).hexdigest()


def _application_notice(body: ApplyBody) -> str:
    """Plain text (sent with parse_mode=None), so nothing the applicant typed can
    be interpreted as Telegram markup. Long free text is truncated."""
    desc = body.product_description
    if len(desc) > 600:
        desc = desc[:600] + " [...]"
    return (
        "New reseller application (stored in reseller_applications, status=new)\n"
        f"Company: {body.company}\n"
        f"Contact: {body.contact_name}\n"
        f"Email: {body.email}\n"
        f"Expected agents: {body.expected_agent_count}\n"
        f"Product: {desc}"
    )


def _send_application_notice(text: str) -> bool:
    from app import notify
    return notify.send_telegram(text, channel=notify.ALERTS, parse_mode=None, timeout=10)


# Mail copy of every stored application. Same Infomaniak submission path and
# credential handling as the contact form (app/contact.py) and the signup
# welcome mail (app/main.py): aiosmtplib with the SMTP_* settings from
# app.main. It runs after the response as a background task, independently of
# the Telegram notice, and never raises: an SMTP problem costs the mail, never
# the stored row, the Telegram notice or the applicant's 202.
RESELLER_APPLY_MAIL_TO = os.getenv("RESELLER_APPLY_MAIL_TO", "info@moltrust.ch")
RESELLER_APPLY_MAIL_TIMEOUT = 20
_HEADER_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f]")


def _header_safe(value: str, limit: int = 200) -> str:
    """Single-line value for a mail header. ApplyBody already rejects CR/LF in
    company and email; this is the second line of defence at the point of use."""
    return _HEADER_UNSAFE_RE.sub(" ", value or "").strip()[:limit]


def _application_mail_body(body: ApplyBody, app_id) -> str:
    """Every submitted field in plain text, plus the stored row id. The id
    appears only here, never in the HTTP response."""
    return (
        "New reseller application on moltrust.ch\n"
        "\n"
        f"Company:         {body.company}\n"
        f"Contact name:    {body.contact_name}\n"
        f"Email:           {body.email}\n"
        f"Expected agents: {body.expected_agent_count}\n"
        "\n"
        "Product description:\n"
        f"{body.product_description}\n"
        "\n"
        "---\n"
        f"Application ID: {app_id}\n"
        "Stored in: reseller_applications (status=new)\n"
        "Reply to this mail to answer the applicant directly.\n"
    )


def _build_application_mail(body: ApplyBody, app_id, sender: str):
    from email.header import Header
    from email.mime.text import MIMEText

    msg = MIMEText(_application_mail_body(body, app_id), "plain", "utf-8")
    msg["From"] = f"MolTrust Reseller <{sender}>"
    msg["To"] = RESELLER_APPLY_MAIL_TO
    reply_to = _header_safe(body.email, 254)
    if _APPLY_EMAIL_RE.match(reply_to):
        msg["Reply-To"] = reply_to
    # Company is applicant-supplied and may be non-ASCII; Header encodes it as
    # RFC 2047 words instead of emitting raw UTF-8.
    msg["Subject"] = str(Header(f"Reseller application: {_header_safe(body.company)}", "utf-8"))
    return msg


async def _send_application_mail(body: ApplyBody, app_id) -> bool:
    """Mail the application to RESELLER_APPLY_MAIL_TO. True only if SMTP
    accepted it. Never raises."""
    try:
        from app.main import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
        if not SMTP_PASS:
            log.warning("reseller apply: SMTP_PASS not set, no mail for id=%s", app_id)
            return False
        import aiosmtplib
        await aiosmtplib.send(
            _build_application_mail(body, app_id, SMTP_USER),
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASS,
            start_tls=True,
            timeout=RESELLER_APPLY_MAIL_TIMEOUT,
        )
        log.info("reseller apply: mail sent id=%s", app_id)
        return True
    except Exception as e:
        # Exception type only: an SMTP error can carry the server dialogue.
        log.error("reseller apply: mail failed id=%s: %s", app_id, type(e).__name__)
        return False


@router.post("/apply", status_code=202)
@_apply_limiter.limit(RESELLER_APPLY_RATE_LIMIT)
async def reseller_apply(request: Request, body: ApplyBody,
                         background_tasks: BackgroundTasks):
    """Accept one reseller application. Stores it, notifies the operator.

    Same 202 body for a genuine application and a honeypot hit; no internal id
    is returned. A Telegram failure never fails the request — the row is
    written first and `notified` records whether the notice went out.
    A mail copy with the row id follows as a background task, after the
    response; it cannot fail the request either.
    """
    import asyncio
    if body.website:
        log.info("reseller apply: honeypot hit")
        return dict(APPLY_ACCEPTED_RESPONSE)
    pool = _pool()
    if not pool:
        raise HTTPException(503, "application intake temporarily unavailable")
    async with pool.acquire() as conn:
        app_id = await conn.fetchval(
            "INSERT INTO reseller_applications "
            "(company, contact_name, email, product_description, expected_agent_count, ip_hash) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            body.company, body.contact_name, body.email, body.product_description,
            body.expected_agent_count, _ip_hash(request),
        )
    sent = False
    try:
        sent = bool(await asyncio.to_thread(_send_application_notice, _application_notice(body)))
    except Exception as e:  # never fail the request on a notification problem
        log.error("reseller apply: telegram notice failed: %s", type(e).__name__)
    if sent:
        try:
            async with pool.acquire() as conn:
                await conn.execute("UPDATE reseller_applications SET notified = true WHERE id = $1", app_id)
        except Exception as e:
            log.error("reseller apply: could not mark notified: %s", type(e).__name__)
    log.info("reseller apply stored id=%s notified=%s", app_id, sent)
    background_tasks.add_task(_send_application_mail, body, app_id)
    return dict(APPLY_ACCEPTED_RESPONSE)

# Reseller-admin (cross-tenant, Lars only) lives in app/reseller_admin.py — it is
# gated on the moltrust.ch/admin session + RESELLER_ADMIN_USERS allowlist + TOTP,
# not the X-Admin-Key. create_reseller/onboard_agent/billing_summary above are the
# reusable primitives it calls.
