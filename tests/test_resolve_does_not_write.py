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


def test_resolve_answers_and_writes_nothing(pool_and_client):
    pool, client = pool_and_client
    r = client.get(f"/identity/resolve/{DID}")
    assert r.status_code == 200, r.text
    writes = [q for q in pool.log if not q.lstrip().upper().startswith("SELECT")]
    assert writes == [], writes
