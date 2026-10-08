"""The issuance and resolution patterns of did:moltrust (decided 2026-10-08).

The issuance pattern is section 2.2 alone; the resolution pattern keeps `ext_`
so the one bridged identifier that carries it stays resolvable. The resolve
test drives the real route through a stand-in pool: it needs app.main
importable and no database.
"""
import ast
import datetime
import pathlib

import pytest

EXT_DID = "did:moltrust:ext_516a656bafa39e5c"
HEX_DID = "did:moltrust:d34ed796a4dc4698"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_issue_pattern_is_section_2_2_alone():
    from app.did_patterns import DID_ISSUE_PATTERN
    assert DID_ISSUE_PATTERN.match(HEX_DID)
    assert not DID_ISSUE_PATTERN.match(EXT_DID)


def test_resolve_pattern_keeps_ext():
    from app.did_patterns import DID_RESOLVE_PATTERN
    assert DID_RESOLVE_PATTERN.match(HEX_DID)
    assert DID_RESOLVE_PATTERN.match(EXT_DID)


@pytest.mark.parametrize("did", [
    "did:moltrust:ambassador0001",
    "did:moltrust:ext_516a656bafa39e5",
    "did:moltrust:EXT_516a656bafa39e5c",
    "did:moltrust:ext516a656bafa39e5c",
])
def test_neither_pattern_widens_beyond_ext(did):
    from app.did_patterns import DID_ISSUE_PATTERN, DID_RESOLVE_PATTERN
    assert not DID_ISSUE_PATTERN.match(did)
    assert not DID_RESOLVE_PATTERN.match(did)


def test_one_definition_everywhere():
    from app.did_patterns import DID_RESOLVE_PATTERN
    from app.main import DID_PATTERN
    from app.enforcement import acceptance_gate, subject_binding
    assert DID_PATTERN is DID_RESOLVE_PATTERN
    assert acceptance_gate._DID_MOLTRUST_RE is DID_RESOLVE_PATTERN
    assert subject_binding._DID_MOLTRUST_RE is DID_RESOLVE_PATTERN


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetchrow(self, query, did):
        return self.rows.get(did)

    async def fetch(self, *a):
        return []

    async def execute(self, *a):
        return "UPDATE 1"


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, rows):
        self.conn = _Conn(rows)

    def acquire(self):
        return _Acquire(self.conn)


def _row(did):
    return {
        "did": did, "display_name": "bridged:agentnexus", "platform": "agentnexus",
        "created_at": datetime.datetime(2026, 4, 22), "wallet_address": None,
        "wallet_chain": None, "wallet_bound_at": None, "public_key_hex": None,
        "key_anchor_tx": None, "key_anchor_block": None, "erc8004_agent_id": None,
    }


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m

    class _Row(dict):
        def keys(self):
            return super().keys()

    rows = {d: _Row(_row(d)) for d in (EXT_DID, HEX_DID)}
    monkeypatch.setattr(m, "db_pool", _Pool(rows))
    return TestClient(m.app)  # no `with`: the lifespan, and its database, stays off


@pytest.mark.parametrize("did", [EXT_DID, HEX_DID])
def test_a_registered_identifier_resolves_with_200(client, did):
    r = client.get(f"/identity/resolve/{did}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == did


def test_ext_identifier_resolves_after_the_split(client):
    """The decision of 2026-10-08, stated as the one observation that matters."""
    r = client.get(f"/identity/resolve/{EXT_DID}")
    assert r.status_code == 200
    assert r.json()["id"] == EXT_DID


def _minted_dids():
    """Every f"did:moltrust:..." the application code builds, by file and line."""
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.JoinedStr) and node.values:
                head = node.values[0]
                if isinstance(head, ast.Constant) and str(head.value).startswith("did:moltrust:"):
                    found.append((str(path.relative_to(ROOT)), str(head.value)))
    return found


def test_the_only_ext_issuer_is_the_known_one():
    """A second place that hands out `ext_` identifiers must show up here.

    app/test_harness/routes.py builds `did:moltrust:ext_...` for the shadow
    agent of an external caller. That is issuance outside DID_ISSUE_PATTERN,
    reported on 2026-10-08 and left for a decision; this test keeps it the
    only one.
    """
    ext = {(p, prefix) for p, prefix in _minted_dids() if prefix.startswith("did:moltrust:ext_")}
    assert ext == {("app/test_harness/routes.py", "did:moltrust:ext_")}
