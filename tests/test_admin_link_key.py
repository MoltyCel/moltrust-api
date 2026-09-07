"""POST /admin/identity/link-key — binding an API key to a DID grants ownership.

Afterwards the key holder can set that DID's first public key through the owner
channel, so this endpoint refuses anything ambiguous instead of guessing, and
records every call.
"""
import os
import uuid

import pytest

ADMIN_HEADER = "x-admin-key"


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.limiter, "enabled", False, raising=False)


@pytest.fixture
def admin_key(monkeypatch):
    key = "admin_" + uuid.uuid4().hex
    monkeypatch.setenv("ADMIN_KEY", key)
    return key


async def _make_agent() -> str:
    from app.main import db_pool
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, agent_type) "
            "VALUES ($1, $2, 'test', 'external')",
            did, f"tc-link-{did[-6:]}",
        )
    return did


async def _make_key(*, active: bool = True, owner: str | None = None) -> str:
    from app.main import db_pool
    key = f"mt_link_{uuid.uuid4().hex}"
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key, email, active, owner_did) VALUES ($1, $2, $3, $4)",
            key, f"tc-link+{key[-6:]}@test.local", active, owner,
        )
    return key


async def _cleanup(dids: list[str], keys: list[str]):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        for k in keys:
            await conn.execute("DELETE FROM api_key_link_audit WHERE key_prefix = $1", k[:8])
            await conn.execute("DELETE FROM api_keys WHERE key = $1", k)
        for d in dids:
            await conn.execute("DELETE FROM api_key_link_audit WHERE did = $1", d)
            await conn.execute("DELETE FROM agents WHERE did = $1", d)


# ---------------------------------------------------------------------------
# the intended path
# ---------------------------------------------------------------------------
async def test_binds_an_unbound_key_and_records_it(async_client, admin_key):
    from app.main import db_pool
    did = await _make_agent()
    key = await _make_key()
    try:
        resp = await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": key, "did": did, "reason": "test"},
            headers={ADMIN_HEADER: admin_key},
        )
        assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"

        body = resp.json()
        assert body["did"] == did
        assert body["key_prefix"] == key[:8]
        assert key not in resp.text, "the key must never be echoed back"

        async with db_pool.acquire() as conn:
            owner = await conn.fetchval("SELECT owner_did FROM api_keys WHERE key = $1", key)
            assert owner == did

            audit = await conn.fetchrow(
                "SELECT did, reason, key_prefix FROM api_key_link_audit WHERE key_prefix = $1",
                key[:8],
            )
            assert audit is not None, "the call must be recorded"
            assert audit["did"] == did
            assert audit["reason"] == "test"
            assert len(audit["key_prefix"]) == 8, "only a prefix is stored"
    finally:
        await _cleanup([did], [key])


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------
async def test_refuses_without_admin_key(async_client, admin_key):
    did = await _make_agent()
    key = await _make_key()
    try:
        resp = await async_client.post(
            "/admin/identity/link-key", json={"api_key": key, "did": did}
        )
        assert resp.status_code == 403

        wrong = await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": key, "did": did},
            headers={ADMIN_HEADER: "admin_wrong"},
        )
        assert wrong.status_code == 403

        from app.main import db_pool
        async with db_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT owner_did FROM api_keys WHERE key = $1", key) is None
    finally:
        await _cleanup([did], [key])


async def test_refuses_an_unknown_key(async_client, admin_key):
    did = await _make_agent()
    try:
        resp = await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": "mt_does_not_exist_at_all", "did": did},
            headers={ADMIN_HEADER: admin_key},
        )
        assert resp.status_code == 404, f"{resp.status_code} {resp.text[:200]}"
    finally:
        await _cleanup([did], [])


async def test_does_not_create_a_key_that_does_not_exist(async_client, admin_key):
    """link_api_key_to_did inserts unknown keys; here that would be a typo."""
    from app.main import db_pool
    did = await _make_agent()
    ghost = f"mt_ghost_{uuid.uuid4().hex}"
    try:
        await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": ghost, "did": did},
            headers={ADMIN_HEADER: admin_key},
        )
        async with db_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT 1 FROM api_keys WHERE key = $1", ghost) is None, \
                "a typo must not mint an API key"
    finally:
        await _cleanup([did], [ghost])


async def test_refuses_an_inactive_key(async_client, admin_key):
    did = await _make_agent()
    key = await _make_key(active=False)
    try:
        resp = await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": key, "did": did},
            headers={ADMIN_HEADER: admin_key},
        )
        assert resp.status_code == 409
        assert "inactive" in resp.text.lower()
    finally:
        await _cleanup([did], [key])


async def test_refuses_a_key_already_bound(async_client, admin_key):
    from app.main import db_pool
    first = await _make_agent()
    second = await _make_agent()
    key = await _make_key(owner=first)
    try:
        resp = await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": key, "did": second},
            headers={ADMIN_HEADER: admin_key},
        )
        assert resp.status_code == 409, f"{resp.status_code} {resp.text[:200]}"

        async with db_pool.acquire() as conn:
            owner = await conn.fetchval("SELECT owner_did FROM api_keys WHERE key = $1", key)
        assert owner == first, "an existing binding must not be overwritten"
    finally:
        await _cleanup([first, second], [key])


async def test_refuses_an_unknown_did(async_client, admin_key):
    from app.main import db_pool
    key = await _make_key()
    try:
        resp = await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": key, "did": "did:moltrust:ffffffffffffffff"},
            headers={ADMIN_HEADER: admin_key},
        )
        assert resp.status_code == 404

        async with db_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT owner_did FROM api_keys WHERE key = $1", key) is None
    finally:
        await _cleanup([], [key])


async def test_refuses_a_malformed_did(async_client, admin_key):
    key = await _make_key()
    try:
        resp = await async_client.post(
            "/admin/identity/link-key",
            json={"api_key": key, "did": "not-a-did"},
            headers={ADMIN_HEADER: admin_key},
        )
        assert resp.status_code == 400
    finally:
        await _cleanup([], [key])


# ---------------------------------------------------------------------------
# Test agents are excluded from the public counters, not deleted
# ---------------------------------------------------------------------------
async def test_public_stats_exclude_test_agents(async_client):
    from app.main import db_pool
    did = await _make_agent()          # platform='test'
    try:
        before = (await async_client.get("/stats")).json()

        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agents (did, display_name, platform, agent_type) "
                "VALUES ($1, $2, 'test', 'external')",
                f"did:moltrust:{uuid.uuid4().hex[:16]}", "tc-link-extra",
            )
        after = (await async_client.get("/stats")).json()

        assert after["agents_total"] == before["agents_total"], \
            "a new test agent must not move the public total"
        assert after["agents_external"] == before["agents_external"]
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM agents WHERE display_name = 'tc-link-extra'")
        await _cleanup([did], [])


def test_the_filter_keeps_rows_with_no_platform():
    """IS DISTINCT FROM, not <> — a NULL platform must still be counted."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, "app", "main.py")).read()
    assert "NOT_TEST_AGENT = \"platform IS DISTINCT FROM 'test'\"" in source
    assert "platform <> 'test'" not in source
