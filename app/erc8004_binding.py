"""ERC-8004 links are re-checked against the token owner (BIND-CHECK 2).

A link between a did:moltrust and an ERC-8004 agentId is made once: either
MolTrust mints the token (owner: the registry signer) or the agent brings one
it holds (autolink, owner: the wallet bound to the DID, confirmed with
ownerOf). An ERC-8004 identity is a transferable ERC-721, so the link stays in
agents.erc8004_agent_id after the token has moved. This module records the
owner at link time and compares it with ownerOf on Base in a recurring run.

States (erc8004_link_owner.status):
  confirmed      ownerOf equals the owner recorded at link time
  owner_changed  ownerOf answers with another address
  token_absent   the registry answers that the token does not exist
  unrecorded     the link predates this table and the current owner is
                 neither the registry signer nor the DID's bound wallet, so
                 there is no owner to compare against

Anything other than confirmed counts as not confirmed: the DID document drops
the ERC-8004 entry from alsoKnownAs and names the state, and the reverse
lookup says the link is not confirmed. An RPC that does not answer changes
nothing and is counted as unreadable, never as confirmed or as a deviation.

agents is postgres-owned, so the state lives in a role-owned side table.
"""
from __future__ import annotations

import datetime
from typing import Callable, Optional

import asyncpg

CONFIRMED = "confirmed"
OWNER_CHANGED = "owner_changed"
TOKEN_ABSENT = "token_absent"
UNRECORDED = "unrecorded"
NOT_CONFIRMED = frozenset({OWNER_CHANGED, TOKEN_ABSENT, UNRECORDED})

ORIGIN_MINTED = "minted"
ORIGIN_AUTOLINK = "autolink"
ORIGIN_BASELINE = "baseline"

# The address that owns the IDs MolTrust mints (app/own_addresses.py, KMS
# registry signer since 2026-10-23). Used only to accept the owner of a link
# that predates this table as its baseline.
REGISTRY_OWNERS = frozenset({"0x8e4cd1e31b4d4114c811e41bd4a01002baec5ee6"})


class OwnerUnreadable(Exception):
    """ownerOf could not be read (RPC did not answer)."""


class TokenAbsent(Exception):
    """The registry answered that the token does not exist."""


async def ensure_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS erc8004_link_owner (
            agent_id      bigint PRIMARY KEY,
            did           varchar(128) NOT NULL,
            linked_owner  varchar(42),
            origin        text NOT NULL,
            recorded_at   timestamptz NOT NULL DEFAULT now(),
            current_owner varchar(42),
            status        text NOT NULL,
            checked_at    timestamptz
        )
    """)


async def record_link(conn, agent_id: int, did: str, owner: str, origin: str) -> None:
    """Owner at the moment of linking. Called by the routes that set the link."""
    await ensure_table(conn)
    owner = (owner or "").lower() or None
    await conn.execute(
        "INSERT INTO erc8004_link_owner (agent_id, did, linked_owner, origin, current_owner, "
        "status, checked_at) VALUES ($1, $2, $3, $4, $3, $5, now()) "
        "ON CONFLICT (agent_id) DO UPDATE SET did = EXCLUDED.did, linked_owner = EXCLUDED.linked_owner, "
        "origin = EXCLUDED.origin, recorded_at = now(), current_owner = EXCLUDED.current_owner, "
        "status = EXCLUDED.status, checked_at = now()",
        int(agent_id), did, owner, origin, CONFIRMED if owner else UNRECORDED)


async def link_state(conn, did: str) -> Optional[dict]:
    """{agent_id, status, checked_at} for the DID's link, None when none is recorded.

    A database without the table answers None: no state recorded is not the
    same as a deviation, and resolution keeps its former shape until the first
    run.
    """
    try:
        row = await conn.fetchrow(
            "SELECT agent_id, status, checked_at FROM erc8004_link_owner WHERE did = $1", did)
    except asyncpg.exceptions.UndefinedTableError:
        return None
    if not row:
        return None
    return {"agent_id": row["agent_id"], "status": row["status"],
            "checked_at": row["checked_at"].isoformat() if row["checked_at"] else None}


async def link_state_by_agent_id(conn, agent_id: int) -> Optional[dict]:
    try:
        row = await conn.fetchrow(
            "SELECT did, status, checked_at FROM erc8004_link_owner WHERE agent_id = $1", int(agent_id))
    except asyncpg.exceptions.UndefinedTableError:
        return None
    if not row:
        return None
    return {"did": row["did"], "status": row["status"],
            "checked_at": row["checked_at"].isoformat() if row["checked_at"] else None}


def read_owner_onchain(agent_id: int) -> str:
    """ownerOf on the Base IdentityRegistry, read only. Raises TokenAbsent or OwnerUnreadable."""
    from app.erc8004 import _is_transport_error, get_identity_contract
    try:
        return get_identity_contract().functions.ownerOf(int(agent_id)).call()
    except Exception as exc:  # noqa: BLE001 - classified below; never read as an owner
        if _is_transport_error(exc):
            raise OwnerUnreadable(type(exc).__name__) from exc
        raise TokenAbsent(str(exc)[:120]) from exc


async def recheck(conn, read_owner: Callable[[int], str] = read_owner_onchain,
                  now: Optional[datetime.datetime] = None) -> dict:
    """Compare ownerOf with the recorded owner for every linked agentId.

    Returns {checked, confirmed, deviations, unreadable, by_status, deviating}
    where `deviating` lists (agent_id, status). `checked` counts links whose
    owner was read; unreadable ones are not in it.
    """
    import asyncio
    await ensure_table(conn)
    now = now or datetime.datetime.now(datetime.UTC)
    links = await conn.fetch(
        "SELECT a.erc8004_agent_id AS agent_id, a.did, lower(a.wallet_address) AS wallet, "
        "o.linked_owner, o.origin FROM agents a "
        "LEFT JOIN erc8004_link_owner o ON o.agent_id = a.erc8004_agent_id "
        "WHERE a.erc8004_agent_id IS NOT NULL ORDER BY a.erc8004_agent_id")
    out = {"linked": len(links), "checked": 0, "confirmed": 0, "deviations": 0,
           "unreadable": 0, "by_status": {}, "deviating": []}
    for link in links:
        agent_id, did = link["agent_id"], link["did"]
        try:
            owner = (await asyncio.to_thread(read_owner, agent_id)).lower()
        except OwnerUnreadable:
            out["unreadable"] += 1
            continue
        except TokenAbsent:
            owner = None
        linked = link["linked_owner"]
        origin = link["origin"]
        if origin is None:
            # Link older than this table: take the owner as baseline only when
            # it is one the link could legitimately have been made with.
            origin = ORIGIN_BASELINE
            if owner and (owner in REGISTRY_OWNERS or (link["wallet"] and owner == link["wallet"])):
                linked = owner
        if owner is None:
            status = TOKEN_ABSENT
        elif linked is None:
            status = UNRECORDED
        elif owner == linked:
            status = CONFIRMED
        else:
            status = OWNER_CHANGED
        await conn.execute(
            "INSERT INTO erc8004_link_owner (agent_id, did, linked_owner, origin, current_owner, "
            "status, checked_at) VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (agent_id) DO UPDATE SET did = EXCLUDED.did, "
            "linked_owner = COALESCE(erc8004_link_owner.linked_owner, EXCLUDED.linked_owner), "
            "current_owner = EXCLUDED.current_owner, status = EXCLUDED.status, "
            "checked_at = EXCLUDED.checked_at",
            int(agent_id), did, linked, origin, owner, status, now)
        out["checked"] += 1
        out["by_status"][status] = out["by_status"].get(status, 0) + 1
        if status == CONFIRMED:
            out["confirmed"] += 1
        else:
            out["deviations"] += 1
            out["deviating"].append((int(agent_id), status))
    return out


def summary_line(r: dict) -> str:
    dev = ", ".join(f"{a}:{s}" for a, s in r["deviating"]) or "-"
    return (f"{r['linked']} verknuepft, {r['checked']} geprueft, {r['deviations']} Abweichungen "
            f"({dev}), {r['unreadable']} nicht lesbar")


async def _cli() -> int:
    """python -m app.erc8004_binding: one run against the live database and Base."""
    import os
    conn = await asyncpg.connect(host=os.getenv("DB_HOST", "localhost"), user="moltstack",
                                 database=os.getenv("DB_NAME", "moltstack"),
                                 password=os.getenv("MOLTSTACK_DB_PW"))
    try:
        r = await recheck(conn)
    finally:
        await conn.close()
    print(summary_line(r))
    return 1 if r["deviations"] else 0


if __name__ == "__main__":
    import asyncio
    import sys
    sys.exit(asyncio.run(_cli()))
