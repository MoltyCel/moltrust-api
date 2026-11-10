"""GET /vc/ipr/{ipr_id}/status checks the id's form before any query (ops IPR-400).

A non-UUID reached check_ipr_status, whose bare uuid.UUID() raised a plain
ValueError past the handler's `except InvalidIprId` and came back as 500. The
nginx log showed 79 of them on the literal placeholder /vc/ipr/%7Bipr_id%7D/status
between 20.10. and 01.11.2026.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as main


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    async def fetchrow(self, *args):
        self.calls.append(args)
        return None


class _Acquire:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        self.calls.append("acquire")
        return _Conn(self.calls)

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """Records every acquire and query; finds nothing."""

    def __init__(self):
        self.calls = []

    def acquire(self):
        return _Acquire(self.calls)


def _ipr_queries(pool):
    """Queries on the IPR table; middleware uses the same pool for its own rows."""
    return [c for c in pool.calls
            if isinstance(c, tuple) and "interaction_proof_records" in str(c[0])]


@pytest.fixture
def pool(monkeypatch):
    p = _Pool()
    monkeypatch.setattr(main, "db_pool", p)
    return p


def _get(path):
    # No `with`: the lifespan would open the real pool.
    return TestClient(main.app).get(path)


@pytest.mark.parametrize("bad", ["nicht-vorhanden-0001",
                                 "ipr_00000000000000000000000000000000"])
def test_non_uuid_is_400_without_query(pool, bad):
    r = _get(f"/vc/ipr/{bad}/status")
    assert r.status_code == 400
    assert "expected a UUID" in r.json()["detail"]
    assert _ipr_queries(pool) == []


def test_placeholder_is_400_without_query(pool):
    r = _get("/vc/ipr/%7Bipr_id%7D/status")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "{ipr_id}" in detail and "expected a UUID" in detail
    assert _ipr_queries(pool) == []


def test_valid_unknown_uuid_stays_404(pool):
    r = _get(f"/vc/ipr/{uuid.uuid4()}/status")
    assert r.status_code == 404
    assert r.json() == {"detail": "IPR not found"}
    assert len(_ipr_queries(pool)) == 1
