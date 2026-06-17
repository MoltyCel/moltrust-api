"""Test fixtures — load secrets from .moltrust_secrets, provide DB connection."""
import os
import pytest
import pytest_asyncio
import asyncpg
import sys

# Load secrets so MOLTRUST_REGISTRY_PRIVATE_KEY is in env BEFORE app modules import
SECRETS = "/home/moltstack/.moltrust_secrets"
if os.path.exists(SECRETS):
    for line in open(SECRETS):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"').strip("'"))

# --- Test database isolation -------------------------------------------------
# Route ALL tests at the sandbox DB, never the live `moltstack` DB. Both the app
# pool (app.main startup) and the per-module test connections read DB_NAME, so
# setting it here — before any of them import — redirects everything. The 402 /
# insufficient-credit INSERTs in the credit middleware would otherwise land in
# the live audit table (paid for: ~20 leaked rows on 2026-06-17). Requires a
# `localhost:5432:moltstack_sandbox:moltstack:*` line in ~/.pgpass.
os.environ.setdefault("DB_NAME", "moltstack_sandbox")

# Hard guard: refuse to run against the live DB unless explicitly acknowledged,
# so a stray DB_NAME=moltstack (or future default drift) can't pollute prod.
if os.environ["DB_NAME"] == "moltstack" and os.environ.get("PYTEST_ALLOW_LIVE_DB") != "1":
    raise RuntimeError(
        "Refusing to run the test suite against the live 'moltstack' database. "
        "Tests default to 'moltstack_sandbox'; set PYTEST_ALLOW_LIVE_DB=1 only if "
        "you really mean to target live."
    )

# Make app importable
sys.path.insert(0, "/home/moltstack/moltstack")


@pytest_asyncio.fixture
async def test_db():
    """Live DB connection, cleaned up after test (deletes any caep_events with did starting did:moltrust:test_)."""
    conn = await asyncpg.connect(
        host="localhost",
        database=os.getenv("DB_NAME", "moltstack"),
        user="moltstack",
    )
    # Pre-clean (in case prior failed test left rows)
    await conn.execute(
        "DELETE FROM caep_events WHERE did LIKE 'did:moltrust:test_%'"
    )
    try:
        yield conn
    finally:
        # Post-clean
        await conn.execute(
            "DELETE FROM caep_events WHERE did LIKE 'did:moltrust:test_%'"
        )
        await conn.close()


# ---------------------------------------------------------------------------
# Credit-middleware test infrastructure
# ---------------------------------------------------------------------------
# Test-DID convention: did:moltrust:<16hex> (29 chars, matches DID_PATTERN
#   `^did:moltrust:(?:ext_)?[a-f0-9]{16}$` so validate_did() accepts test DIDs).
# Marker for analytics filtering lives in agents.display_name (prefix 'tc-')
# and agents.platform='test'. Per-test rows in agents + credit_balances + api_keys
# are cleaned up via the fixture's `created` list (not by DID-prefix pattern).
# credit_transactions is append-only by trigger — test rows are LEFT in place;
# they can be filtered out of analytics by joining agents on from_did and
# filtering display_name LIKE 'tc-%' or platform = 'test'.


@pytest_asyncio.fixture
async def app_with_lifespan():
    """Import the FastAPI app and trigger startup so db_pool is initialized.

    The on_event('startup') hook in app.main allocates the global db_pool;
    httpx.AsyncClient + ASGITransport does NOT trigger lifespan automatically,
    so we run the registered handlers manually.
    """
    from app.main import app
    for handler in getattr(app.router, "on_startup", []):
        await handler()
    yield app
    # Note: on_shutdown handlers intentionally NOT run — process exit handles
    # pool cleanup; running them now would close the pool before later fixtures.


@pytest_asyncio.fixture
async def async_client(app_with_lifespan):
    """httpx.AsyncClient over ASGITransport against the live app."""
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app_with_lifespan)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def credit_test_agent(app_with_lifespan):
    """Factory: creates a test agent with starting balance, returns (did, api_key).

    Inserts into agents, credit_balances, api_keys + adds the test key to the
    in-memory API_KEYS env-set so verify_api_key() accepts it.

    Cleanup (FK order): api_keys → credit_balances → agents. credit_transactions
    rows from middleware-driven deducts are intentionally NOT cleaned (append-only
    trigger). Marker for downstream analytics filtering: agents.display_name LIKE
    'tc-%' and agents.platform = 'test' — join on from_did to find test ledger rows.
    """
    import uuid as _uuid
    from app.main import db_pool, API_KEYS

    created: list[tuple[str, str]] = []

    async def _make(balance: int = 1000):
        did = f"did:moltrust:{_uuid.uuid4().hex[:16]}"
        api_key = f"mt_tc_{_uuid.uuid4().hex}"
        display_name = f"tc-{did[-8:]}"
        async with db_pool.acquire() as conn:
            # Wrap all three INSERTs in one transaction so a failure in any
            # of them rolls back the others — no half-created test agents.
            # The created.append() below only runs if the whole tx commits.
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agents (did, display_name, platform, agent_type) "
                    "VALUES ($1, $2, 'test', 'external')",
                    did, display_name,
                )
                await conn.execute(
                    "INSERT INTO credit_balances (did, balance) VALUES ($1, $2)",
                    did, balance,
                )
                await conn.execute(
                    "INSERT INTO api_keys (key, email, owner_did) VALUES ($1, $2, $3)",
                    api_key, f"test+{did[-8:]}@test.local", did,
                )
        API_KEYS.add(api_key)
        created.append((did, api_key))
        return did, api_key

    yield _make

    async with db_pool.acquire() as conn:
        for did, api_key in created:
            # insufficient_credit_events is written by the credit middleware on
            # 402 (its own committed connection) and is NOT FK-cleaned — remove
            # the test rows so the suite doesn't accrete 402 noise in the table.
            await conn.execute("DELETE FROM insufficient_credit_events WHERE did = $1", did)
            await conn.execute("DELETE FROM api_keys WHERE owner_did = $1", did)
            await conn.execute("DELETE FROM credit_balances WHERE did = $1", did)
            await conn.execute("DELETE FROM agents WHERE did = $1", did)
            API_KEYS.discard(api_key)
