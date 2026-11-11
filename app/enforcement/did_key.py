"""The did:moltrust key in force, read from the pinned registry (ops V6).

One answer to "which key may sign for this DID right now", shared by the AAE
acceptance gate and the request-signature check, so the two cannot disagree.

Pinned means the agents table and agent_key_history, never a network fetch at
decision time. What does not resolve is refused, not assumed:

- no agents row, or a row without public_key_hex: refused;
- agents.revoked_at set: refused, the DID is deactivated (method spec section
  4.4); the gate read only public_key_hex before and accepted a revoked DID's
  signature;
- the key in force is agents.public_key_hex, numbered one past the retired
  keys in agent_key_history (app/key_rotation.py). The gate hard-coded
  #key-1, which would have refused every signature after a DID's first
  rotation and kept accepting the retired key's id. A retired key is refused
  by name.
"""

from __future__ import annotations

import asyncpg

from app.did_patterns import DID_RESOLVE_PATTERN


class KeyResolutionError(ValueError):
    """The DID has no key that may sign now. The message names the reason."""


async def current_key(conn, did: str) -> tuple[str, bytes]:
    """(key id, 32-byte Ed25519 public key) of the key in force for `did`."""
    if not DID_RESOLVE_PATTERN.match(did or ""):
        raise KeyResolutionError("not a valid did:moltrust")
    row = await conn.fetchrow(
        "SELECT public_key_hex, revoked_at FROM agents WHERE did = $1", did)
    if not row:
        raise KeyResolutionError("DID not resolvable: not registered")
    if row["revoked_at"] is not None:
        raise KeyResolutionError("DID is revoked")
    if not row["public_key_hex"]:
        raise KeyResolutionError("DID not resolvable: no registered key")
    try:
        raw = bytes.fromhex(row["public_key_hex"])
    except ValueError:
        raise KeyResolutionError("registered public key is not valid hex")
    if len(raw) != 32:
        raise KeyResolutionError("registered key is not a 32-byte Ed25519 public key")
    try:
        retired = await conn.fetchval(
            "SELECT COUNT(*) FROM agent_key_history WHERE did = $1", did)
    except asyncpg.exceptions.UndefinedTableError:
        retired = 0
    return f"{did}#key-{retired + 1}", raw


async def require_current_key(conn, did: str, kid: str) -> bytes:
    """The public key for `kid`, if `kid` names the key in force for `did`."""
    key_id, raw = await current_key(conn, did)
    if kid != key_id:
        prefix = f"{did}#key-"
        index = kid[len(prefix):] if kid.startswith(prefix) else ""
        if index.isdigit() and int(index) < int(key_id[len(prefix):]):
            raise KeyResolutionError(f"key {kid} is retired; the key in force is {key_id}")
        raise KeyResolutionError(f"kid does not reference the key in force ({key_id})")
    return raw
