"""Who may make the registry sign, or the productive wallet send, for whom.

Two checks, used by every route that issues a credential for a DID or starts a
transaction from the productive signer:

- ``require_bound_key``: the API key has to belong to an agent. A key from an
  email signup with no agent behind it identifies nobody a signature or a
  transaction could be attributed to.
- ``require_did_owner_or_admin``: the key has to own the DID the route acts
  for, or the caller presents the admin key. This is the check
  POST /compliance/declaration has always made (``_require_did_owner_or_admin``
  in app/main.py now delegates here), so ownership means the same on every
  route that uses it.

Both run before anything is signed, written, charged or scheduled. The 403 for
a foreign DID is the same whether the DID belongs to someone else or does not
exist, so the answer is no oracle for which DIDs are registered.
"""
from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request

from app.credits import resolve_did_from_api_key

NOT_OWNER_DETAIL = "Not authorized for this DID"
NO_AGENT_DETAIL = (
    "This API key is not bound to an agent. Bind it to your DID first: "
    "POST /auth/signup-did, or register one: POST /identity/register."
)


def is_admin(request: Request) -> bool:
    """True when the request carries the configured admin key."""
    admin_key = request.headers.get("x-admin-key", "")
    expected = os.environ.get("ADMIN_KEY", "")
    return bool(expected and admin_key and secrets.compare_digest(admin_key, expected))


async def caller_did(pool, api_key: str) -> str | None:
    if not pool:
        raise HTTPException(503, "Database unavailable")
    async with pool.acquire() as conn:
        return await resolve_did_from_api_key(conn, api_key)


async def require_bound_key(pool, api_key: str) -> str:
    """The DID that owns ``api_key``; 403 when the key has no agent."""
    did = await caller_did(pool, api_key)
    if not did:
        raise HTTPException(403, NO_AGENT_DETAIL)
    return did


async def require_did_owner_or_admin(request: Request, pool, api_key: str,
                                     subject_did: str) -> None:
    """Pass when the caller is admin or ``api_key`` owns ``subject_did``.

    A key with no agent is refused with the same 403 as a wrong owner: it owns
    no DID, so it owns this one neither. Admin bypasses the lookup.
    """
    if is_admin(request):
        return
    did = await caller_did(pool, api_key)
    if not did or did != subject_did:
        raise HTTPException(403, NOT_OWNER_DETAIL)
