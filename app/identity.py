"""Identity resolution for probe + claimed agents.

Probes are auto-minted on keyless requests so first-touch flows do not hit the
legacy signup gate. Probes have a 24h TTL, a 50-call cap, and a tool
authorization ceiling enforced by `require_probe` / `require_claimed`.

See docs/auto-probe-token-spec.md §3 (lifecycle), §4.2 (this module),
§4.4 (probe key emission), §6 (auth matrix), §10.2 (TTL extension).
"""
from __future__ import annotations

import hashlib
import os
import secrets as _secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
from fastapi import HTTPException, Request


PROBE_KEY_PREFIX = "mt_probe_"
PROBE_DID_PREFIX = "did:moltrust:probe:"
PROBE_TTL = timedelta(hours=24)
PROBE_CALL_CAP_DEFAULT = 50
PROBE_TTL_EXTENSION = timedelta(hours=12)
PROBE_TTL_MAX_EXTENSIONS = 2


class AuthError(Exception):
    """Raised by resolve_identity for an invalid/expired key.

    `status` is the HTTP status the middleware should surface (401 invalid,
    410 expired, 429 over-cap).
    """

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Identity:
    """Resolved identity for the current request.

    `kind`:
      - "claimed"    — permanent agent (env key, env+DB key, or DB api_keys row)
      - "probe"      — existing probe, supplied mt_probe_* via X-API-Key
      - "probe-new"  — auto-minted on this very request; `probe_key` set
    """

    kind: str
    did: str
    api_key: Optional[str] = None
    probe_key: Optional[str] = None
    probe: Optional[dict] = field(default=None, repr=False)

    @property
    def is_probe(self) -> bool:
        return self.kind in ("probe", "probe-new")

    @property
    def is_claimed(self) -> bool:
        return self.kind == "claimed"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def hash_session(session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    if request.client:
        return request.client.host
    return None


def _generate_probe_did() -> str:
    return PROBE_DID_PREFIX + _secrets.token_hex(4)


def _generate_probe_key() -> str:
    return PROBE_KEY_PREFIX + _secrets.token_hex(16)


def env_api_keys() -> set[str]:
    raw = os.getenv("MOLTRUST_API_KEYS", "")
    return set(filter(None, (k.strip() for k in raw.split(","))))


async def _lookup_probe_by_key_hash(conn, key_hash: str) -> Optional[dict]:
    row = await conn.fetchrow(
        "SELECT did, expires_at, call_count, call_cap, ttl_extensions, "
        "claimed_at, claimed_did, first_seen_ip, first_seen_ua, "
        "smithery_session_hash, created_at "
        "FROM probe_agents WHERE probe_key_hash = $1",
        key_hash,
    )
    return dict(row) if row else None


async def _mint_probe(
    conn,
    *,
    ip: Optional[str],
    ua: Optional[str],
    smithery_session_hash: Optional[str],
) -> tuple[str, str, dict]:
    """Insert a fresh probe row; return (did, raw_key, row). Retries on DID collision."""
    expires_at = datetime.now(tz=timezone.utc) + PROBE_TTL
    last_err: Optional[Exception] = None
    for _ in range(5):
        did = _generate_probe_did()
        key = _generate_probe_key()
        key_hash = hash_key(key)
        try:
            row = await conn.fetchrow(
                "INSERT INTO probe_agents (did, probe_key_hash, expires_at, "
                "first_seen_ip, first_seen_ua, smithery_session_hash) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "RETURNING did, expires_at, call_count, call_cap, ttl_extensions, "
                "claimed_at, claimed_did, first_seen_ip, first_seen_ua, "
                "smithery_session_hash, created_at",
                did, key_hash, expires_at, ip, ua, smithery_session_hash,
            )
            return did, key, dict(row)
        except asyncpg.UniqueViolationError as exc:
            last_err = exc
            continue
    raise RuntimeError(f"Could not mint a unique probe DID after retries: {last_err}")


async def resolve_identity(request: Request, conn) -> Identity:
    """Resolve the identity for `request`. Mints a probe when no key is supplied.

    Precedence:
      1. X-API-Key starting with mt_probe_ -> probe lookup (probe_agents table)
      2. X-API-Key in env MOLTRUST_API_KEYS -> claimed (legacy env key)
      3. X-API-Key matching api_keys.key -> claimed (DB-issued)
      4. No X-API-Key -> mint fresh probe
    """
    api_key = request.headers.get("x-api-key", "")
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")
    smithery_session_hash = hash_session(request.headers.get("mcp-session-id"))

    if api_key:
        if api_key.startswith(PROBE_KEY_PREFIX):
            probe = await _lookup_probe_by_key_hash(conn, hash_key(api_key))
            if not probe:
                raise AuthError("Invalid probe key", status=401)
            if probe["claimed_at"]:
                raise AuthError(
                    "This probe was claimed — use the permanent mt_* key issued at claim",
                    status=410,
                )
            now = datetime.now(tz=timezone.utc)
            if probe["expires_at"] < now:
                raise AuthError("Probe expired — POST /auth/claim to keep, or sign up fresh", status=410)
            if probe["call_count"] >= probe["call_cap"]:
                raise AuthError(
                    "Probe call cap reached — POST /auth/claim to keep history, or sign up",
                    status=429,
                )
            return Identity(kind="probe", did=probe["did"], api_key=api_key, probe=probe)

        if api_key in env_api_keys():
            return Identity(kind="claimed", did="legacy:env", api_key=api_key)

        owner_did = await conn.fetchval(
            "SELECT owner_did FROM api_keys WHERE key = $1 AND COALESCE(active, true) = true",
            api_key,
        )
        if owner_did:
            return Identity(kind="claimed", did=owner_did, api_key=api_key)
        raise AuthError("Invalid API key", status=401)

    did, key, row = await _mint_probe(
        conn,
        ip=ip,
        ua=ua,
        smithery_session_hash=smithery_session_hash,
    )
    return Identity(kind="probe-new", did=did, api_key=key, probe_key=key, probe=row)


async def increment_probe_call_count(conn, did: str) -> int:
    """Atomic increment. Returns the new call_count (caller may compare against cap)."""
    return await conn.fetchval(
        "UPDATE probe_agents SET call_count = call_count + 1 WHERE did = $1 "
        "RETURNING call_count",
        did,
    )


async def maybe_extend_probe_ttl(conn, did: str) -> bool:
    """Auto-extend an active probe at ≥80% of its TTL, up to 2 times (spec §10.2).

    Returns True if extended on this call.
    """
    row = await conn.fetchrow(
        "SELECT created_at, expires_at, ttl_extensions FROM probe_agents WHERE did = $1",
        did,
    )
    if not row or row["ttl_extensions"] >= PROBE_TTL_MAX_EXTENSIONS:
        return False
    total = (row["expires_at"] - row["created_at"]).total_seconds()
    elapsed = (datetime.now(tz=timezone.utc) - row["created_at"]).total_seconds()
    if total <= 0 or elapsed / total < 0.8:
        return False
    await conn.execute(
        "UPDATE probe_agents SET expires_at = expires_at + $1, "
        "ttl_extensions = ttl_extensions + 1 WHERE did = $2",
        PROBE_TTL_EXTENSION, did,
    )
    return True


CLAIM_CURL = (
    "curl -X POST https://api.moltrust.ch/auth/claim "
    "-H 'Content-Type: application/json' "
    "-d '{\"probe_key\":\"mt_probe_<your-key>\",\"email\":\"you@example.com\"}'"
)


def get_identity(request: Request) -> Identity:
    """Return the request's resolved Identity. Raises 500 if middleware did not run."""
    identity: Optional[Identity] = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(
            status_code=500,
            detail="Identity middleware did not resolve a request identity",
        )
    return identity


def require_claimed(request: Request) -> Identity:
    """Depends-guard for endpoints that touch money or the production trust graph.

    Probes (incl. fresh probe-new) get a structured 401 with the exact curl needed
    to claim. Per spec §6.3.
    """
    identity = get_identity(request)
    if not identity.is_claimed:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Claimed identity required for this operation",
                "reason": "probes cannot touch money or the production trust graph",
                "claim_url": "https://api.moltrust.ch/auth/claim",
                "claim_curl": CLAIM_CURL,
                "probe_did": identity.did,
            },
        )
    return identity


def require_probe(request: Request) -> Identity:
    """Depends-guard for endpoints that need any resolved identity (probe or claimed).

    Identity middleware already guarantees an identity is present, so this is
    effectively a no-op pass-through useful for explicit documentation in
    route signatures.
    """
    return get_identity(request)


async def get_probe_summary(conn, probe_did: str) -> dict:
    """Activity counters used for the dynamic `claim_value` pitch in moltrust_identity.

    Light: counts only — actual evidence stays in probe_activity / credentials tables.
    """
    counts = await conn.fetchrow(
        "SELECT COUNT(*) AS tool_calls, "
        "COUNT(DISTINCT tool_name) AS unique_tools "
        "FROM probe_activity WHERE probe_did = $1",
        probe_did,
    )
    verticals = await conn.fetchval(
        "SELECT COUNT(DISTINCT "
        "CASE WHEN tool_name LIKE 'moltrust\\_%' ESCAPE '\\' THEN 'moltrust' "
        "     WHEN tool_name LIKE 'moltguard\\_%' ESCAPE '\\' THEN 'moltguard' "
        "     WHEN tool_name LIKE 'mt\\_%' ESCAPE '\\' THEN "
        "          split_part(tool_name, '_', 2) END) "
        "FROM probe_activity WHERE probe_did = $1",
        probe_did,
    ) or 0
    credentials_received = await conn.fetchval(
        "SELECT COUNT(*) FROM credentials WHERE subject_did = $1",
        probe_did,
    ) or 0
    return {
        "tool_calls": counts["tool_calls"] if counts else 0,
        "unique_tools": counts["unique_tools"] if counts else 0,
        "verticals_touched": verticals,
        "credentials_received": credentials_received,
    }
