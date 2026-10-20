"""GET /identity/resolve/{did} reads and writes nothing about the agent.

Until 2026-10-08 it called update_last_seen(), so every lookup by anyone made
the agent look active. Measured that day: the last_seen of
did:moltrust:d34ed796a4dc4698 and did:moltrust:ext_516a656bafa39e5c had been
set at 02:17 UTC by our own artefact-check and conformance runs, and again at
06:20 by a manual probe.

Drives the real route through a stand-in pool that records every statement;
needs app.main importable and no database.
"""
import datetime

import pytest

DID = "did:moltrust:d34ed796a4dc4698"


class _Row(dict):
    def keys(self):
        return super().keys()


class _Conn:
    def __init__(self, log):
        self.log = log

    async def fetchrow(self, query, *args):
        self.log.append(query)
        return _Row(did=DID, display_name="t", platform="test",
                    created_at=datetime.datetime(2026, 4, 1), wallet_address=None,
                    wallet_chain=None, wallet_bound_at=None, public_key_hex=None,
                    key_anchor_tx=None, key_anchor_block=None, erc8004_agent_id=None)

    async def fetch(self, query, *args):
        self.log.append(query)
        return []

    async def execute(self, query, *args):
        self.log.append(query)
        return "UPDATE 1"


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        return _Acquire(_Conn(self.log))


@pytest.fixture
def pool_and_client(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m

    pool = _Pool()
    monkeypatch.setattr(m, "db_pool", pool)
    return pool, TestClient(m.app)  # no `with`: the lifespan stays off


READ_ROUTES = {
    "resolve_did": "/identity/resolve/{did:path}",
    "verify_agent": "/identity/verify/{did}",
    "get_identity_badge": "/identity/badge/{did}",
    "erc8004_registration_file": "/agents/{did}/erc8004",
}


def _route_functions():
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    out = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in READ_ROUTES:
            paths = [d.args[0].value for d in node.decorator_list
                     if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)]
            out[node.name] = (paths, node)
    return out


@pytest.mark.parametrize("name", sorted(READ_ROUTES))
def test_read_route_does_not_mark_the_agent_seen(name):
    """Read from the source: none of the four lookups calls a last_seen writer."""
    import ast

    paths, node = _route_functions()[name]
    assert READ_ROUTES[name] in paths
    called = {
        c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", None)
        for c in ast.walk(node) if isinstance(c, ast.Call)
    }
    assert not called & {"update_last_seen", "touch_last_seen", "update_last_active"}
    strings = [c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    assert not any("last_seen" in s and "UPDATE" in s.upper() for s in strings)


def test_resolve_answers_and_writes_nothing(pool_and_client):
    pool, client = pool_and_client
    r = client.get(f"/identity/resolve/{DID}")
    assert r.status_code == 200, r.text
    writes = [q for q in pool.log if not q.lstrip().upper().startswith("SELECT")]
    assert writes == [], writes
