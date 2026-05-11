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

# Anti-abuse caps for fresh probe minting per spec §8.
PROBE_SPAWN_PER_IP_PER_HOUR = 5
PROBE_SPAWN_PER_SUBNET_PER_HOUR = 20


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


async def _lookup_active_probe_by_session_hash(conn, session_hash: str) -> Optional[dict]:
    """Find an active (unexpired, unclaimed, under cap) probe for a session hash.

    Used to keep a single probe across multiple keyless calls in one MCP session,
    so within-session activity accumulates on one DID instead of fragmenting.
    """
    row = await conn.fetchrow(
        "SELECT did, expires_at, call_count, call_cap, ttl_extensions, "
        "claimed_at, claimed_did, first_seen_ip, first_seen_ua, "
        "smithery_session_hash, created_at "
        "FROM probe_agents "
        "WHERE smithery_session_hash = $1 "
        "  AND claimed_at IS NULL "
        "  AND expires_at > now() "
        "  AND call_count < call_cap "
        "ORDER BY created_at DESC LIMIT 1",
        session_hash,
    )
    return dict(row) if row else None


async def _enforce_spawn_rate(conn, ip: Optional[str]) -> None:
    """Block probe-farm spawn behavior per spec §8.

    5 fresh probes per IP per hour; 20 per IPv4 /24 per hour. IPv6 falls back
    to the per-IP limit only — covering /64 abuse cleanly is left as a
    follow-up (would need an inet mask helper).
    """
    if not ip:
        return
    per_ip = await conn.fetchval(
        "SELECT COUNT(*) FROM probe_agents "
        "WHERE first_seen_ip = $1::inet AND created_at > now() - interval '1 hour'",
        ip,
    ) or 0
    if per_ip >= PROBE_SPAWN_PER_IP_PER_HOUR:
        raise AuthError("Probe spawn rate limit (per IP) exceeded — try again later", status=429)
    if "." in ip and ":" not in ip:  # IPv4
        subnet = ".".join(ip.split(".")[:3]) + ".0/24"
        per_subnet = await conn.fetchval(
            "SELECT COUNT(*) FROM probe_agents "
            "WHERE first_seen_ip << $1::inet AND created_at > now() - interval '1 hour'",
            subnet,
        ) or 0
        if per_subnet >= PROBE_SPAWN_PER_SUBNET_PER_HOUR:
            raise AuthError("Probe spawn rate limit (per subnet) exceeded — try again later", status=429)


async def _mint_probe(
    conn,
    *,
    ip: Optional[str],
    ua: Optional[str],
    smithery_session_hash: Optional[str],
) -> tuple[str, str, dict]:
    """Insert a fresh probe row; return (did, raw_key, row). Retries on DID collision."""
    await _enforce_spawn_rate(conn, ip)
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

    # Within an MCP session, keep the same probe across multiple keyless calls
    # so activity accumulates on one DID. Outside MCP, this is a no-op.
    if smithery_session_hash:
        existing = await _lookup_active_probe_by_session_hash(conn, smithery_session_hash)
        if existing:
            return Identity(kind="probe", did=existing["did"], probe=existing)

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


EMAIL_RE = __import__("re").compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PROBE_GRACE = timedelta(days=7)
PROBE_CLAIM_CREDIT_GRANT = 175


class ClaimError(Exception):
    """Raised by claim_probe for invalid input. `status` maps to HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
        self.message = message


async def claim_probe(
    conn,
    *,
    probe_key: str,
    email: Optional[str],
    display_name: Optional[str],
    ip: Optional[str],
) -> dict:
    """Promote a probe to a permanent agent. email=None → anonymous_claimed tier.

    Returns dict with did, api_key, tier, credits, status, message. Raises
    ClaimError on invalid probe_key, claimed/expired probe, or bad email format.
    Email collisions return the existing identity (idempotent claim).
    """
    probe = await conn.fetchrow(
        "SELECT did, expires_at, claimed_at, claimed_did "
        "FROM probe_agents WHERE probe_key_hash = $1",
        hash_key(probe_key),
    )
    if not probe:
        raise ClaimError("Invalid probe key", status=401)
    if probe["claimed_at"]:
        raise ClaimError(
            f"Probe already claimed as {probe['claimed_did']} — use that mt_* key",
            status=410,
        )
    now = datetime.now(tz=timezone.utc)
    if probe["expires_at"] < now - PROBE_GRACE:
        raise ClaimError("Probe expired beyond 7-day grace period", status=410)

    email_hash: Optional[str] = None
    if email is not None:
        email = email.lower().strip()
        if not EMAIL_RE.match(email):
            raise ClaimError("Invalid email format", status=400)
        email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()
        existing = await conn.fetchrow(
            "SELECT key, owner_did FROM api_keys "
            "WHERE email = $1 AND COALESCE(active, true) = true AND owner_did IS NOT NULL "
            "LIMIT 1",
            email,
        )
        if existing:
            return {
                "did": existing["owner_did"],
                "api_key": existing["key"],
                "status": "existing_identity_returned",
                "message": "Email already registered — using existing identity.",
            }

    new_did = f"did:moltrust:{_secrets.token_hex(8)}"
    new_key = f"mt_{_secrets.token_hex(16)}"
    if not display_name:
        display_name = email.split("@")[0] if email else f"probe-claimed-{new_did[-8:]}"
    display_name = display_name[:64]
    tier = "anonymous_claimed" if email is None else "standard"
    email_for_keys = email or f"anonymous+{new_did[-8:]}@moltrust.ch"

    async with conn.transaction():
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, agent_type, "
            "registration_ip, parent_probe_did) "
            "VALUES ($1, $2, 'moltrust', 'external', $3, $4)",
            new_did, display_name, ip, probe["did"],
        )
        await conn.execute(
            "INSERT INTO api_keys (key, email, owner_did, tier) VALUES ($1, $2, $3, $4)",
            new_key, email_for_keys, new_did, tier,
        )
        await conn.execute(
            "UPDATE probe_agents SET claimed_at = now(), claimed_did = $1, "
            "claimed_email_hash = $2 WHERE did = $3",
            new_did, email_hash, probe["did"],
        )
        await conn.execute(
            "INSERT INTO conversion_funnel (probe_did, claim_state, claimed_at) "
            "VALUES ($1, $2, now()) "
            "ON CONFLICT (probe_did) DO UPDATE "
            "SET claim_state = EXCLUDED.claim_state, claimed_at = EXCLUDED.claimed_at",
            probe["did"], "anonymous-claimed" if email is None else "claimed",
        )
        # Credit grant — lazy-imported to keep identity.py independent of credits stack
        try:
            from app.credits import ensure_balance_row, grant_credits  # noqa: WPS433
            await ensure_balance_row(conn, new_did, 0)
            await grant_credits(conn, new_did, PROBE_CLAIM_CREDIT_GRANT, "probe_claim", "Free credits on probe claim")
            credits_granted = PROBE_CLAIM_CREDIT_GRANT
        except Exception:
            credits_granted = 0

    return {
        "did": new_did,
        "api_key": new_key,
        "tier": tier,
        "credits": credits_granted,
        "status": "claimed",
        "claimed_from_probe": probe["did"],
        "message": (
            "Probe claimed. Use the new mt_* key as X-API-Key for all future calls. "
            "Your probe history is preserved on the new DID."
        ),
    }


def build_claim_value_pitch(summary: dict) -> str:
    """Dynamic claim-pitch from a probe summary. Empty probe → encouragement.

    Per spec §4.4: pitch grows more concrete as the probe accumulates work, so
    a probe at 80% call-cap has been doing real work and resistance is low.
    """
    tools = summary.get("tool_calls", 0)
    if tools == 0:
        return (
            "Your probe has not used any tools yet. "
            "Try one — your history will accumulate here and claim keeps it permanent."
        )
    parts = [f"{tools} tool call" + ("s" if tools != 1 else "")]
    unique = summary.get("unique_tools", 0)
    if unique > 1:
        parts.append(f"{unique} distinct tools")
    verticals = summary.get("verticals_touched", 0)
    if verticals > 1:
        parts.append(f"{verticals} verticals touched")
    creds = summary.get("credentials_received", 0)
    if creds > 0:
        parts.append(f"{creds} credential" + ("s" if creds != 1 else "") + " received")
    return (
        f"Your probe has accumulated {', '.join(parts)}. "
        "Claim now to keep this history attached to your permanent DID."
    )


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
