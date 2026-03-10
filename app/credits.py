"""MolTrust Credits — Internal credit ledger for API monetisation."""

import re

# ---------------------------------------------------------------------------
# Endpoint pricing table (credits per call)
# ---------------------------------------------------------------------------

ENDPOINT_COSTS = {
    # Free tier
    "GET /health": 0,
    "GET /identity/resolve/{did}": 0,
    "GET /.well-known/did.json": 0,
    "GET /.well-known/agent.json": 0,
    "GET /agents/recent": 0,
    "GET /stats": 0,
    "GET /credits/pricing": 0,
    "GET /credits/balance/{did}": 0,
    "GET /credits/transactions/{did}": 0,
    "POST /credits/transfer": 0,
    "GET /join": 0,
    "POST /auth/signup": 0,
    "POST /auth/moltbook": 0,
    "GET /auth/github": 0,
    "GET /auth/github/callback": 0,
    "GET /skills": 0,
    "POST /payment/lightning/invoice": 0,

    # Paid — Identity
    "POST /identity/register": 1,
    "GET /identity/verify/{did}": 1,

    # Paid — Credentials (heavier compute)
    "POST /credentials/issue": 2,
    "POST /credentials/verify": 2,

    # Paid — Reputation
    "GET /reputation/query/{did}": 1,
    "POST /reputation/rate": 1,

    # Paid — A2A
    "GET /a2a/agent-card/{did}": 1,

    # Sports
    "GET /sports/health": 0,
    "POST /sports/predictions/commit": 1,
    "GET /sports/predictions/verify/{hash}": 0,
    "GET /sports/predictions/history/{did}": 1,
    "PATCH /sports/predictions/settle/{hash}": 0,

    # Signal Providers
    "POST /sports/signals/register": 2,
    "GET /sports/signals/verify/{id}": 0,
    "GET /sports/signals/leaderboard": 0,
    "GET /sports/signals/badge/{id}": 0,

    # Fantasy Lineups
    "POST /sports/fantasy/lineups/commit": 1,
    "GET /sports/fantasy/lineups/verify/{hash}": 0,
    "PATCH /sports/fantasy/lineups/settle/{hash}": 0,
    "GET /sports/fantasy/history/{did}": 1,
}

# Patterns to collapse concrete paths into pricing keys
_ROUTE_PATTERNS = [
    (re.compile(r"^/identity/verify/(.+)$"), "/identity/verify/{did}"),
    (re.compile(r"^/identity/resolve/(.+)$"), "/identity/resolve/{did}"),
    (re.compile(r"^/reputation/query/(.+)$"), "/reputation/query/{did}"),
    (re.compile(r"^/a2a/agent-card/(.+)$"), "/a2a/agent-card/{did}"),
    (re.compile(r"^/credits/balance/(.+)$"), "/credits/balance/{did}"),
    (re.compile(r"^/credits/transactions/(.+)$"), "/credits/transactions/{did}"),
    (re.compile(r"^/sports/predictions/verify/(.+)$"), "/sports/predictions/verify/{hash}"),
    (re.compile(r"^/sports/predictions/history/(.+)$"), "/sports/predictions/history/{did}"),
    (re.compile(r"^/sports/predictions/settle/(.+)$"), "/sports/predictions/settle/{hash}"),
    (re.compile(r"^/sports/signals/verify/(.+)$"), "/sports/signals/verify/{id}"),
    (re.compile(r"^/sports/signals/badge/(.+)$"), "/sports/signals/badge/{id}"),
    (re.compile(r"^/sports/fantasy/lineups/verify/(.+)$"), "/sports/fantasy/lineups/verify/{hash}"),
    (re.compile(r"^/sports/fantasy/lineups/settle/(.+)$"), "/sports/fantasy/lineups/settle/{hash}"),
    (re.compile(r"^/sports/fantasy/history/(.+)$"), "/sports/fantasy/history/{did}"),
]


def resolve_endpoint_key(method: str, path: str) -> str:
    """Map a concrete request path to its pricing key."""
    for pattern, template in _ROUTE_PATTERNS:
        if pattern.match(path):
            return f"{method} {template}"
    return f"{method} {path}"


def get_endpoint_cost(method: str, path: str) -> int:
    """Return the credit cost for a request. Unknown endpoints default to 0."""
    key = resolve_endpoint_key(method, path)
    return ENDPOINT_COSTS.get(key, 0)


# ---------------------------------------------------------------------------
# Database helpers (all take an asyncpg connection)
# ---------------------------------------------------------------------------

async def get_balance(conn, did: str) -> int:
    """Return the credit balance for a DID, or 0 if no row exists."""
    row = await conn.fetchval(
        "SELECT balance FROM credit_balances WHERE did = $1", did
    )
    return row if row is not None else 0


async def ensure_balance_row(conn, did: str, initial: int = 0):
    """Create a balance row if it doesn't exist yet."""
    await conn.execute(
        "INSERT INTO credit_balances (did, balance) VALUES ($1, $2) ON CONFLICT (did) DO NOTHING",
        did, initial,
    )


async def grant_credits(conn, did: str, amount: int, reference: str, description: str):
    """Add credits to an agent and log the transaction."""
    await conn.execute(
        "UPDATE credit_balances SET balance = balance + $1, updated_at = NOW() WHERE did = $2",
        amount, did,
    )
    balance_after = await get_balance(conn, did)
    await conn.execute(
        """INSERT INTO credit_transactions
           (from_did, to_did, amount, tx_type, reference, description, balance_after)
           VALUES (NULL, $1, $2, 'grant', $3, $4, $5)""",
        did, amount, reference, description, balance_after,
    )


async def deduct_credits(conn, did: str, amount: int, reference: str):
    """Deduct credits atomically. Raises ValueError if insufficient funds."""
    row = await conn.fetchrow(
        "SELECT balance FROM credit_balances WHERE did = $1 FOR UPDATE", did
    )
    if row is None or row["balance"] < amount:
        current = row["balance"] if row else 0
        raise ValueError(f"Insufficient credits: have {current}, need {amount}")
    new_balance = row["balance"] - amount
    await conn.execute(
        "UPDATE credit_balances SET balance = $1, updated_at = NOW() WHERE did = $2",
        new_balance, did,
    )
    await conn.execute(
        """INSERT INTO credit_transactions
           (from_did, to_did, amount, tx_type, reference, description, balance_after)
           VALUES ($1, NULL, $2, 'api_call', $3, $4, $5)""",
        did, amount, reference, f"API call: {reference}", new_balance,
    )


async def transfer_credits(conn, from_did: str, to_did: str, amount: int, reference: str):
    """Atomic agent-to-agent transfer. Locks rows in sorted order to prevent deadlocks."""
    dids = sorted([from_did, to_did])
    # Lock both rows in deterministic order
    for d in dids:
        await conn.fetchrow(
            "SELECT balance FROM credit_balances WHERE did = $1 FOR UPDATE", d
        )

    sender_balance = await get_balance(conn, from_did)
    if sender_balance < amount:
        raise ValueError(f"Insufficient credits: have {sender_balance}, need {amount}")

    new_sender = sender_balance - amount
    await conn.execute(
        "UPDATE credit_balances SET balance = $1, updated_at = NOW() WHERE did = $2",
        new_sender, from_did,
    )

    receiver_balance = await get_balance(conn, to_did)
    new_receiver = receiver_balance + amount
    await conn.execute(
        "UPDATE credit_balances SET balance = $1, updated_at = NOW() WHERE did = $2",
        new_receiver, to_did,
    )

    # Log both sides
    await conn.execute(
        """INSERT INTO credit_transactions
           (from_did, to_did, amount, tx_type, reference, description, balance_after)
           VALUES ($1, $2, $3, 'transfer', $4, $5, $6)""",
        from_did, to_did, amount, reference, f"Transfer to {to_did}", new_sender,
    )
    await conn.execute(
        """INSERT INTO credit_transactions
           (from_did, to_did, amount, tx_type, reference, description, balance_after)
           VALUES ($1, $2, $3, 'transfer', $4, $5, $6)""",
        from_did, to_did, amount, reference, f"Transfer from {from_did}", new_receiver,
    )


async def get_transactions(conn, did: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Return transaction history for a DID, newest first."""
    rows = await conn.fetch(
        """SELECT id, from_did, to_did, amount, tx_type, reference, description,
                  balance_after, created_at
           FROM credit_transactions
           WHERE from_did = $1 OR to_did = $1
           ORDER BY created_at DESC
           LIMIT $2 OFFSET $3""",
        did, limit, offset,
    )
    return [
        {
            "id": r["id"],
            "from_did": r["from_did"],
            "to_did": r["to_did"],
            "amount": r["amount"],
            "tx_type": r["tx_type"],
            "reference": r["reference"],
            "description": r["description"],
            "balance_after": r["balance_after"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# API key ↔ DID linking
# ---------------------------------------------------------------------------

async def resolve_did_from_api_key(conn, key: str) -> str | None:
    """Return the owner_did for an API key, or None."""
    return await conn.fetchval(
        "SELECT owner_did FROM api_keys WHERE key = $1", key
    )


async def link_api_key_to_did(conn, key: str, did: str):
    """Link an API key to an agent DID (only if not already linked).

    Handles both DB-issued keys and env-var hardcoded keys:
    if the key doesn't exist in api_keys yet, insert it first.
    """
    result = await conn.execute(
        "UPDATE api_keys SET owner_did = $1 WHERE key = $2 AND owner_did IS NULL",
        did, key,
    )
    if result == "UPDATE 0":
        # Key might not be in DB (env-var key) — insert it, then link
        await conn.execute(
            "INSERT INTO api_keys (key, email, owner_did) VALUES ($1, $2, $3) ON CONFLICT (key) DO UPDATE SET owner_did = $3 WHERE api_keys.owner_did IS NULL",
            key, "env-hardcoded", did,
        )
