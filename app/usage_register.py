"""Registration on use: one call, a DID and a free API key (ops WM-3).

Every other way to an identity here takes three calls and client-side
cryptography: a challenge, a proof of work, an Ed25519 key the caller generates
and keeps, then a second signed call for a key. Callers that reach us without an
identity mostly do not do that. This path asks for nothing: POST an empty body,
get back a did:moltrust and an API key bound to it.

What it gives and what it does not:

- An opaque identifier, like the other routes that hold no proved key. There is
  no key binding, so nothing here can later be presented as proof of key
  possession; an agent that wants that registers through /identity/register-pop.
- A free API key. The key reaches the free tier (app/free_tier.py: the hourly
  allowance on read and verify endpoints) and grants zero spendable credits, as
  keyless registration does. The monthly credit floor is not applied to these
  DIDs: a floor per anonymous one-call mint is farmable in the same way a
  registration grant would be (see `is_usage_registration`).
- No on-chain anchor and no credential. An anonymous mint should not spend gas
  out of the productive signer.

Abuse limits, in the order they bind:

1. Idempotent per caller fingerprint. The same client address with the same
   User-Agent gets the same DID back for IDEMPOTENCY_DAYS, so a prober that
   calls a hundred times adds one agent, not a hundred. A repeat call re-issues
   the key: the previous key issued by this path for that DID is retired and a
   new one returned. Keys are shown once; re-issuing is how a caller that lost
   its key gets back in without a second agent.
2. A per-IP rate limit on the route (RATE_LIMIT), which bounds a caller that
   rotates its User-Agent to defeat (1).

The fingerprint is stored as a hash, never as the address or the header. It is
not a secret-keyed hash: the input space of an IPv4 address times a common
User-Agent is small, so the hash is pseudonymous rather than anonymous, and the
row carries nothing else about the caller.
"""
from __future__ import annotations

import hashlib
import secrets

# The platform value this path writes. It joins the usage bucket next to the
# values our client packages send (app/sql/registry_export.sql, ops WM-2).
PLATFORM = "usage-api"

# Every platform that counts in the origin group "usage". One list for the
# agent_counts column, the registry export and the daily stats; the tests check
# all three against it.
USAGE_PLATFORMS = ("usage-x402", "usage-enforce", "usage-mcp", "paperclip", PLATFORM)

# A caller may ask for platform=test, so our own live probes register without
# counting (agent_counts.sql deducts platform test) and without any anchor.
ALLOWED_PLATFORMS = (PLATFORM, "test")

IDEMPOTENCY_DAYS = 30
RATE_LIMIT = "10/hour"
SIGNUP_METHOD = "usage_register"
DEFAULT_DISPLAY_NAME = "usage-agent"

_FP_LABEL = b"usage-register-v1|"
_UA_MAX = 256


def fingerprint(ip: str | None, user_agent: str | None) -> str:
    """Hash of (client address, User-Agent). Hex, 64 characters."""
    ua = (user_agent or "")[:_UA_MAX]
    raw = _FP_LABEL + (ip or "").encode("utf-8", "replace") + b"|" + ua.encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()


def new_key() -> str:
    """Same shape as /auth/signup and /auth/signup-did."""
    return f"mt_{secrets.token_hex(16)}"


async def ensure_usage_register_tables(conn) -> None:
    """Role-owned side table: `agents` is postgres-owned and takes no columns."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_registrations (
            fingerprint   TEXT PRIMARY KEY,
            did           TEXT        NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            calls         INTEGER     NOT NULL DEFAULT 1
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_registrations_did ON usage_registrations (did)"
    )


async def existing_did(conn, fp: str) -> str | None:
    """The DID this fingerprint registered inside the window, if still live."""
    return await conn.fetchval(
        f"""
        SELECT u.did FROM usage_registrations u
          JOIN agents a ON a.did = u.did
         WHERE u.fingerprint = $1
           AND u.created_at > now() - interval '{IDEMPOTENCY_DAYS} days'
           AND a.revoked_at IS NULL
        """,
        fp,
    )


async def remember(conn, fp: str, did: str) -> None:
    """Bind the fingerprint to a new DID, replacing an expired binding."""
    await conn.execute(
        """
        INSERT INTO usage_registrations (fingerprint, did) VALUES ($1, $2)
        ON CONFLICT (fingerprint) DO UPDATE SET
            did = EXCLUDED.did, created_at = now(), last_seen_at = now(), calls = 1
        """,
        fp, did,
    )


async def touch(conn, fp: str) -> None:
    await conn.execute(
        "UPDATE usage_registrations SET last_seen_at = now(), calls = calls + 1 "
        "WHERE fingerprint = $1",
        fp,
    )


async def retire_keys(conn, did: str) -> list[str]:
    """Deactivate the keys this path issued for `did`; return them for eviction.

    Only keys with SIGNUP_METHOD are touched: a key the agent got another way
    (e.g. /auth/signup-did) is not this path's to retire.
    """
    rows = await conn.fetch(
        "UPDATE api_keys SET active = false "
        "WHERE owner_did = $1 AND signup_method = $2 AND active = true RETURNING key",
        did, SIGNUP_METHOD,
    )
    return [r["key"] for r in rows]


async def issue_key(conn, did: str) -> str:
    key = new_key()
    await conn.execute(
        "INSERT INTO api_keys (key, owner_did, signup_method) VALUES ($1, $2, $3)",
        key, did, SIGNUP_METHOD,
    )
    return key


async def is_usage_registration(conn, did: str) -> bool:
    """True when `did` came from this path. Read by the monthly credit floor.

    Only a table that does not exist yet answers False: that is the state
    before this module was deployed, when no DID can have come from here. Any
    other failure raises, and the caller ends up without the floor rather than
    with one it should not have had.
    """
    import asyncpg
    try:
        return bool(await conn.fetchval(
            "SELECT 1 FROM usage_registrations WHERE did = $1 LIMIT 1", did))
    except asyncpg.exceptions.UndefinedTableError:
        return False
