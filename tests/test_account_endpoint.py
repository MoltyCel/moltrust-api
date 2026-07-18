"""GET /account — resolve payer_ref from the API key (possession proof), never
from an email (no auth leak). Sandbox only."""
import uuid
import pytest
import pytest_asyncio

import app.main as _m
from app.main import API_KEYS
from app import accounts


def _pool():
    return _m.db_pool


@pytest_asyncio.fixture
async def acct(app_with_lifespan):
    keys, payers = [], []

    async def mk():
        key = f"mt_tc_{uuid.uuid4().hex}"
        email = f"tc{uuid.uuid4().hex[:10]}@gmail.com"
        async with _pool().acquire() as conn:
            await conn.execute("INSERT INTO api_keys (key, email) VALUES ($1, $2)", key, email)
            pr = await accounts.create_account_for_key(conn, key, email)
        API_KEYS.add(key)
        keys.append(key); payers.append(pr)
        return key, email, pr

    yield mk

    async with _pool().acquire() as conn:
        for k in keys:
            await conn.execute("DELETE FROM api_keys WHERE key = $1", k)
            API_KEYS.discard(k)
        for pr in payers:
            await conn.execute("DELETE FROM accounts WHERE payer_ref = $1", pr)


# valid key -> its OWN account (email + payer_ref)
async def test_valid_key_returns_own_account(acct, async_client):
    key, email, pr = await acct()
    r = await async_client.get("/account", headers={"X-API-Key": key})
    assert r.status_code == 200, r.text[:200]
    d = r.json()
    assert d["payer_ref"] == pr
    assert d["email"] == email


# invalid key -> 403, no account
async def test_invalid_key_rejected(async_client):
    r = await async_client.get("/account", headers={"X-API-Key": "mt_bogus_" + uuid.uuid4().hex})
    assert r.status_code == 403, r.text[:200]
    assert "payer_ref" not in r.text


# knowing the victim's EMAIL + a bogus key must NOT surface the payer_ref
async def test_email_cannot_leak_foreign_payer_ref(acct, async_client):
    key, email, pr = await acct()  # victim account exists
    r = await async_client.get(
        f"/account?email={email}",
        headers={"X-API-Key": "mt_bogus_" + uuid.uuid4().hex},
    )
    assert r.status_code == 403, "email must not be usable to fetch a foreign payer_ref"
    assert pr not in r.text
