"""Reseller-portal ADMIN access (Phase 2, 2026-07-19).

One cross-tenant admin role (Lars) that deliberately bypasses reseller tenant
isolation to see/operate ALL resellers. Because it is all-powerful it is gated
harder than any reseller login, on THREE independent factors:

  1. A valid moltrust.ch/admin session (username/password → app.admin_auth,
     in-memory SESSIONS). No second password — this is the existing admin login.
  2. The session username is on the RESELLER_ADMIN_USERS allowlist (env).
     **Fail-closed:** empty/unset allowlist => nobody. Set it to a comma-separated
     list of admin usernames exactly as they appear in MOLTRUST_ADMIN_USERS (the
     part before the first colon), e.g. RESELLER_ADMIN_USERS="lars". Comparison is
     case-insensitive.
  3. A confirmed TOTP second factor, verified at a step-up (POST .../elevate) that
     mints a short-lived elevated token. The data/action endpoints require that
     elevated token — there is NO path to reseller-admin data without TOTP.

Enrollment (first time, no secret yet) is protected by factors (1)+(2) only — the
verified existing admin auth + allowlist — because TOTP cannot yet exist. This is
built on the checked Ist-Stand (admin auth = username/password session), not a
guess. RE-enrollment (a confirmed secret already exists) additionally requires a
valid current TOTP code, so a password-only attacker cannot overwrite the secret.

TOTP secret is encrypted at rest (pgcrypto pgp_sym_encrypt) under
RESELLER_ADMIN_TOTP_KEY (env). **Fail-closed:** if that key is unset, enrollment
and verification are impossible → no reseller-admin access at all.
"""
import os
import re
import hmac
import time
import base64
import struct
import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from app.admin_auth import verify_session
from app import reseller as R
from app.reseller_invoice import create_reseller_invoice

log = logging.getLogger("reseller_admin")

ELEVATED_TTL_MIN = int(os.getenv("RESELLER_ADMIN_ELEVATED_TTL_MIN", "30"))
_ISSUER = "MolTrust Reseller Admin"


def _pool():
    import app.main as _m
    return _m.db_pool


# ---------------------------------------------------------------------------
# Config gates (fail-closed)
# ---------------------------------------------------------------------------
def admin_allowlist() -> set:
    """Lowercased usernames allowed into reseller-admin. Empty => nobody."""
    raw = os.getenv("RESELLER_ADMIN_USERS", "")
    return {u.strip().lower() for u in raw.split(",") if u.strip()}


def _totp_key() -> str | None:
    return os.getenv("RESELLER_ADMIN_TOTP_KEY") or None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# TOTP (RFC 6238, SHA1, 30s, 6 digits) — pure stdlib, no new dependency
# ---------------------------------------------------------------------------
def _b32_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret_b32: str, ts: float, step: int = 30, digits: int = 6) -> str:
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    counter = int(ts // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    if not (code and str(code).isdigit()):
        return False
    code = str(code).zfill(6)
    now = time.time()
    for w in range(-window, window + 1):
        if hmac.compare_digest(totp_at(secret_b32, now + w * 30), code):
            return True
    return False


def otpauth_uri(username: str, secret_b32: str) -> str:
    from urllib.parse import quote
    label = quote(f"{_ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(_ISSUER)}&digits=6&period=30"


# ---------------------------------------------------------------------------
# Schema (mirrors 2026-07-19_reseller_admin.sql up)
# ---------------------------------------------------------------------------
async def ensure_reseller_admin_tables(conn):
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reseller_admin_2fa (
            username        TEXT PRIMARY KEY,
            totp_secret_enc BYTEA NOT NULL,
            confirmed       BOOLEAN NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reseller_admin_sessions (
            token_sha256 TEXT PRIMARY KEY,
            username     TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at   TIMESTAMPTZ NOT NULL,
            revoked      BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_admin_sessions_user ON reseller_admin_sessions(username)")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reseller_admin_audit (
            id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor TEXT NOT NULL, action TEXT NOT NULL, target_payer_ref TEXT,
            detail JSONB, row_hash TEXT
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_admin_audit_actor ON reseller_admin_audit(actor)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reseller_admin_audit_target ON reseller_admin_audit(target_payer_ref)")
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION reseller_admin_audit_bind_hash() RETURNS trigger AS $$
        BEGIN
          NEW.row_hash := 'sha256:' || encode(digest(
            coalesce(to_char(NEW.ts, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), '') || '|' ||
            NEW.actor || '|' || NEW.action || '|' || coalesce(NEW.target_payer_ref,'') || '|' ||
            coalesce(NEW.detail::text,''), 'sha256'), 'hex');
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """
    )
    await conn.execute("DROP TRIGGER IF EXISTS trg_reseller_admin_audit_bind ON reseller_admin_audit")
    await conn.execute(
        "CREATE TRIGGER trg_reseller_admin_audit_bind BEFORE INSERT ON reseller_admin_audit "
        "FOR EACH ROW EXECUTE FUNCTION reseller_admin_audit_bind_hash()"
    )
    await conn.execute(
        """
        CREATE OR REPLACE FUNCTION reseller_admin_audit_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'reseller_admin_audit is append-only: % forbidden', TG_OP; END;
        $$ LANGUAGE plpgsql
        """
    )
    await conn.execute("DROP TRIGGER IF EXISTS trg_reseller_admin_audit_immutable ON reseller_admin_audit")
    await conn.execute(
        "CREATE TRIGGER trg_reseller_admin_audit_immutable BEFORE UPDATE OR DELETE ON reseller_admin_audit "
        "FOR EACH ROW EXECUTE FUNCTION reseller_admin_audit_immutable()"
    )


async def _audit(conn, actor, action, target_payer_ref=None, detail=None):
    import json
    await conn.execute(
        "INSERT INTO reseller_admin_audit (actor, action, target_payer_ref, detail) VALUES ($1,$2,$3,$4)",
        actor, action, target_payer_ref, json.dumps(detail) if detail is not None else None,
    )


# ---------------------------------------------------------------------------
# TOTP storage (encrypted at rest)
# ---------------------------------------------------------------------------
async def _store_secret(conn, username, secret_b32, key):
    await conn.execute(
        "INSERT INTO reseller_admin_2fa (username, totp_secret_enc, confirmed, updated_at) "
        "VALUES ($1, pgp_sym_encrypt($2, $3), false, now()) "
        "ON CONFLICT (username) DO UPDATE SET "
        "totp_secret_enc = pgp_sym_encrypt($2, $3), confirmed = false, updated_at = now()",
        username, secret_b32, key,
    )


async def _read_secret(conn, username, key):
    """Return (secret_b32, confirmed) or (None, False). Decrypt failures => (None,False)."""
    row = await conn.fetchrow("SELECT totp_secret_enc, confirmed FROM reseller_admin_2fa WHERE username = $1", username)
    if not row:
        return None, False
    try:
        secret = await conn.fetchval("SELECT pgp_sym_decrypt($1, $2)", row["totp_secret_enc"], key)
    except Exception:
        return None, False
    return secret, bool(row["confirmed"])


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------
def _admin_session(request: Request):
    """Existing moltrust.ch/admin session (Bearer or admin_token cookie)."""
    token = request.headers.get("authorization", "").replace("Bearer ", "").strip()
    if not token:
        token = request.cookies.get("admin_token", "")
    return verify_session(token) if token else None


async def require_listed_admin(request: Request) -> str:
    """Factors (1)+(2): valid admin session AND username on the allowlist.

    Gates ENROLLMENT / step-up. 401 if no admin session, 403 if not allowlisted.
    Fail-closed: empty allowlist => 403 for everyone.
    """
    sess = _admin_session(request)
    if not sess:
        raise HTTPException(401, "admin authentication required")
    username = (sess.get("username") or "").lower()
    if username not in admin_allowlist():
        raise HTTPException(403, "not authorized for reseller admin")
    return username


async def require_reseller_admin(request: Request) -> str:
    """Full gate for data/actions: a valid elevated (TOTP-verified) token whose
    username is STILL on the allowlist. The elevated token only exists if TOTP
    was verified at /elevate, so this cannot be reached without the second factor.
    """
    token = request.headers.get("x-reseller-admin-token", "").strip()
    if not token:
        raise HTTPException(401, "reseller-admin elevation required")
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT username, expires_at, revoked FROM reseller_admin_sessions WHERE token_sha256 = $1",
            _hash_token(token),
        )
    if not row or row["revoked"] or datetime.now(timezone.utc) > row["expires_at"]:
        raise HTTPException(401, "reseller-admin elevation required")
    if (row["username"] or "").lower() not in admin_allowlist():
        raise HTTPException(403, "not authorized for reseller admin")
    return row["username"]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/admin/reseller", tags=["reseller-admin"])


class CodeBody(BaseModel):
    code: str


class CreateBody(BaseModel):
    login: str
    password: str
    wholesale_price_cents: int
    display_name: str | None = None
    email: str | None = None
    vat_id: str | None = None


class AssignBody(BaseModel):
    did: str


class InvoiceBody(BaseModel):
    finalize: bool = False   # default draft-only; live faktura stays flag-off elsewhere


@router.get("/2fa/status")
async def totp_status(username: str = Depends(require_listed_admin)):
    key = _totp_key()
    if not key:
        return {"enrolled": False, "confirmed": False, "key_configured": False}
    async with _pool().acquire() as conn:
        secret, confirmed = await _read_secret(conn, username, key)
    return {"enrolled": secret is not None, "confirmed": confirmed, "key_configured": True}


@router.post("/2fa/enroll")
async def totp_enroll(request: Request, username: str = Depends(require_listed_admin)):
    key = _totp_key()
    if not key:
        raise HTTPException(503, "RESELLER_ADMIN_TOTP_KEY not configured (fail-closed)")
    async with _pool().acquire() as conn:
        cur_secret, cur_confirmed = await _read_secret(conn, username, key)
        # Re-enrollment of a CONFIRMED secret requires a valid current code.
        if cur_confirmed:
            body = {}
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not totp_verify(cur_secret or "", str(body.get("code", ""))):
                raise HTTPException(403, "current TOTP code required to re-enroll")
        new_secret = _b32_secret()
        await _store_secret(conn, username, new_secret, key)
        await _audit(conn, username, "2fa_enroll_start")
    return {"secret": new_secret, "otpauth_uri": otpauth_uri(username, new_secret),
            "note": "Add to an authenticator app, then POST /admin/reseller/2fa/confirm with a code."}


@router.post("/2fa/confirm")
async def totp_confirm(body: CodeBody, username: str = Depends(require_listed_admin)):
    key = _totp_key()
    if not key:
        raise HTTPException(503, "RESELLER_ADMIN_TOTP_KEY not configured (fail-closed)")
    async with _pool().acquire() as conn:
        secret, _ = await _read_secret(conn, username, key)
        if not secret:
            raise HTTPException(400, "no enrollment in progress")
        if not totp_verify(secret, body.code):
            raise HTTPException(403, "invalid TOTP code")
        await conn.execute("UPDATE reseller_admin_2fa SET confirmed = true, updated_at = now() WHERE username = $1", username)
        await _audit(conn, username, "2fa_confirmed")
    return {"confirmed": True}


@router.post("/elevate")
async def elevate(body: CodeBody, username: str = Depends(require_listed_admin)):
    key = _totp_key()
    if not key:
        raise HTTPException(503, "RESELLER_ADMIN_TOTP_KEY not configured (fail-closed)")
    async with _pool().acquire() as conn:
        secret, confirmed = await _read_secret(conn, username, key)
        if not (secret and confirmed):
            raise HTTPException(403, "TOTP enrollment required before elevation")
        if not totp_verify(secret, body.code):
            raise HTTPException(403, "invalid TOTP code")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=ELEVATED_TTL_MIN)
        await conn.execute(
            "INSERT INTO reseller_admin_sessions (token_sha256, username, expires_at) VALUES ($1,$2,$3)",
            _hash_token(token), username, expires,
        )
        await _audit(conn, username, "elevate")
    return {"token": token, "expires_at": expires.isoformat()}


# ---- data / actions (require elevated token) ------------------------------
@router.get("/list")
async def list_resellers(actor: str = Depends(require_reseller_admin)):
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.payer_ref, r.login, r.display_name, r.wholesale_price_cents, r.currency,
                   r.customer_vat_id, r.active,
                   (SELECT count(*) FROM agent_payer ap WHERE ap.payer_ref = r.payer_ref
                      AND EXISTS(SELECT 1 FROM agents a WHERE a.did = ap.did)) AS active_count,
                   (SELECT count(*) FROM agent_payer ap WHERE ap.payer_ref = r.payer_ref
                      AND NOT EXISTS(SELECT 1 FROM agents a WHERE a.did = ap.did)) AS pending_count
            FROM reseller_accounts r ORDER BY r.created_at
            """
        )
    return {"resellers": [{
        "payer_ref": x["payer_ref"], "login": x["login"], "display_name": x["display_name"],
        "wholesale_price_cents": int(x["wholesale_price_cents"]), "currency": x["currency"],
        "customer_vat_id": x["customer_vat_id"], "active": x["active"],
        "active_count": int(x["active_count"]),
        "pending_count": int(x["pending_count"]),
        "month_total_cents": int(x["active_count"]) * int(x["wholesale_price_cents"]),
    } for x in rows]}


@router.get("/tenant/{payer_ref}")
async def reseller_detail(payer_ref: str, actor: str = Depends(require_reseller_admin)):
    async with _pool().acquire() as conn:
        return await R.billing_summary(conn, payer_ref)


@router.post("/create")
async def admin_create_reseller(body: CreateBody, actor: str = Depends(require_reseller_admin)):
    async with _pool().acquire() as conn:
        try:
            payer_ref = await R.create_reseller(
                conn, body.login, body.password, body.wholesale_price_cents,
                display_name=body.display_name, email=body.email, vat_id=body.vat_id,
            )
        except ValueError as e:
            raise HTTPException(409, str(e))
        await _audit(conn, actor, "create_reseller", target_payer_ref=payer_ref,
                     detail={"login": (body.login or "").strip().lower()})
    return {"payer_ref": payer_ref, "login": (body.login or "").strip().lower()}


@router.post("/tenant/{payer_ref}/agents")
async def admin_assign_agent(payer_ref: str, body: AssignBody, actor: str = Depends(require_reseller_admin)):
    async with _pool().acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM reseller_accounts WHERE payer_ref = $1", payer_ref)
        if not exists:
            raise HTTPException(404, "reseller not found")
        result = await R.onboard_agent(conn, payer_ref, body.did.strip(), actor=f"admin:{actor}")
        await _audit(conn, actor, "assign_agent", target_payer_ref=payer_ref, detail={"did": body.did.strip(), "result": result})
    return {"did": body.did.strip(), "status": result}


@router.post("/tenant/{payer_ref}/invoice")
async def admin_draft_invoice(payer_ref: str, body: InvoiceBody, actor: str = Depends(require_reseller_admin)):
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT r.wholesale_price_cents, r.display_name, r.customer_vat_id, a.email, a.stripe_customer_id "
            "FROM reseller_accounts r JOIN accounts a ON a.payer_ref = r.payer_ref WHERE r.payer_ref = $1",
            payer_ref,
        )
        if not row:
            raise HTTPException(404, "reseller not found")
        count = await conn.fetchval(
            "SELECT count(*) FROM agent_payer ap WHERE ap.payer_ref = $1 "
            "AND EXISTS(SELECT 1 FROM agents a WHERE a.did = ap.did)", payer_ref) or 0
        await _audit(conn, actor, "draft_invoice", target_payer_ref=payer_ref,
                     detail={"count": int(count), "finalize": bool(body.finalize)})
    # Stripe call outside the DB connection. Test-key-only + live-faktura flag lives in reseller_invoice.
    result = create_reseller_invoice(
        count=int(count), wholesale_price_cents=int(row["wholesale_price_cents"]), currency="eur",
        email=row["email"], display_name=row["display_name"], existing_customer_id=row["stripe_customer_id"],
        recipient_vat_id=row["customer_vat_id"], finalize=bool(body.finalize),
    )
    return result
