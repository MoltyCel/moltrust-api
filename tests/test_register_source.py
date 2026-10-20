"""POST /identity/register with and without `source` (#863, ops REG-SRC).

register_agent read body.source from 27.09.2026 (#504) on, while
RegisterRequest did not declare it. Every keyed registration that got past the
gates wrote the agents row and then raised AttributeError. Two tests: the
route itself, and a static check that every body.<field> a register route
reads is declared on its request model, so the next undeclared read fails
here and not in production.
"""
import ast
import pathlib
import uuid

import pytest_asyncio

import app.main as _m
from app.main import API_KEYS, PopRegisterRequest, RegisterRequest
from app import accounts

MAIN = pathlib.Path(__file__).resolve().parent.parent / "app" / "main.py"


def _pool():
    return _m.db_pool


@pytest_asyncio.fixture
async def keyed(app_with_lifespan):
    key = f"mt_rs_{uuid.uuid4().hex}"
    email = f"rs{uuid.uuid4().hex[:10]}@gmail.com"  # public domain => domain-gate skipped
    async with _pool().acquire() as conn:
        await conn.execute("INSERT INTO api_keys (key, email) VALUES ($1, $2)", key, email)
        payer_ref = await accounts.create_account_for_key(conn, key, email)
    API_KEYS.add(key)
    dids = []
    yield key, dids

    async with _pool().acquire() as conn:
        for d in dids:
            await conn.execute("DELETE FROM agent_source WHERE did = $1", d)
            await conn.execute("DELETE FROM agent_payer WHERE did = $1", d)
        await conn.execute("DELETE FROM agent_payer WHERE payer_ref = $1", payer_ref)
        await conn.execute("DELETE FROM accounts WHERE payer_ref = $1", payer_ref)
        await conn.execute("DELETE FROM api_keys WHERE key = $1", key)
    API_KEYS.discard(key)


async def _register(async_client, key, **extra):
    body = {"display_name": f"rs-{uuid.uuid4().hex[:6]}", "platform": "test", **extra}
    return await async_client.post("/identity/register", headers={"X-API-Key": key}, json=body)


async def test_register_with_source_succeeds_and_records_it(async_client, keyed):
    key, dids = keyed
    r = await _register(async_client, key, source="mcp")
    assert r.status_code < 300, r.text
    did = r.json()["did"]
    dids.append(did)
    async with _pool().acquire() as conn:
        src = await conn.fetchval("SELECT source FROM agent_source WHERE did = $1", did)
        vc = await conn.fetchval(
            "SELECT count(*) FROM credentials WHERE subject_did = $1 AND credential_type = 'AgentTrustCredential'",
            did,
        )
    assert src == "mcp"
    assert vc == 1, "the steps after the agents row must have run"


async def test_register_without_source_succeeds(async_client, keyed):
    key, dids = keyed
    r = await _register(async_client, key)
    assert r.status_code < 300, r.text
    did = r.json()["did"]
    dids.append(did)
    async with _pool().acquire() as conn:
        src = await conn.fetchval("SELECT source FROM agent_source WHERE did = $1", did)
    assert src is None


async def test_register_rejects_an_overlong_source(async_client, keyed):
    key, _ = keyed
    r = await _register(async_client, key, source="x" * 65)
    assert r.status_code == 422


def _body_reads(func_name: str) -> set[str]:
    tree = ast.parse(MAIN.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            return {
                n.attr for n in ast.walk(node)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "body"
            }
    raise AssertionError(f"{func_name} not found in app/main.py")


def test_register_routes_read_only_declared_fields():
    for func, model in (("register_agent", RegisterRequest), ("register_agent_pop", PopRegisterRequest)):
        reads = _body_reads(func)
        assert reads, f"{func} reads no body field; the check would prove nothing"
        undeclared = reads - set(model.model_fields)
        assert not undeclared, f"{func} reads undeclared {model.__name__} fields: {sorted(undeclared)}"


def test_the_static_check_catches_an_undeclared_read():
    # Negative case: the check must fail on the shape #863 had.
    reads = {"display_name", "platform", "source"}
    declared = set(RegisterRequest.model_fields) - {"source"}
    assert reads - declared == {"source"}
