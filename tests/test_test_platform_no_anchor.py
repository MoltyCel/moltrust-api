"""A registration with platform=test never signs with the productive key.

`anchor_to_base` and `register_onchain_agent` both sign with BASE_WRITE_KEY,
the wallet that owns the eight ERC-8004 IDs. Our own probes (funnel_test.py,
gate_proof.py) register with platform=test, and until this file every one of
them sent an anchor transaction from that wallet.

Three layers:
- the predicate (app/anchor_guard.py), with its negative cases;
- the routes, with a stand-in pool and the signing functions replaced by
  recorders, for platform=test (never called, tx_hash None, status says so)
  and for another platform (called once);
- a static check that no route in app/main.py reaches the signing functions
  except through `anchor_registration` / `erc8004_register_for_platform`, so a
  new register route cannot skip the guard.

No database: same stand-in as tests/test_register_referral.py.
"""
import ast
import contextlib
import pathlib
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import accounts, agent_profile, anchor_guard, erc8004, referral

MAIN = pathlib.Path(__file__).resolve().parent.parent / "app" / "main.py"
FAKE_TX = "0x" + "ab" * 32


# --- predicate ---------------------------------------------------------------

@pytest.mark.parametrize("platform", ["test", "TEST", " Test ", "\ttest\n"])
def test_is_test_platform_matches_trimmed_and_case_insensitive(platform):
    assert anchor_guard.is_test_platform(platform)


@pytest.mark.parametrize("platform", [None, "", "a2a", "moltbook", "testnet",
                                      "own_test", "te st", "gate"])
def test_is_test_platform_leaves_other_platforms_alone(platform):
    assert not anchor_guard.is_test_platform(platform)


# --- stand-in pool -----------------------------------------------------------

class Conn:
    def __init__(self, db):
        self.db = db

    async def fetchval(self, sql, *args):
        return None if "FROM api_keys" in sql else 0

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        if sql.startswith("UPDATE agents SET base_tx_hash"):
            self.db.base_tx_hash[args[1]] = args[0]
        if sql.startswith("UPDATE agents SET erc8004_agent_id"):
            self.db.erc8004_id[args[1]] = args[0]
        return "OK"

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self):
        self.base_tx_hash, self.erc8004_id = {}, {}

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield Conn(self)


@pytest.fixture
def env(monkeypatch):
    pool = Pool()
    calls = {"anchor": [], "erc8004": []}

    async def none(*a, **k):
        return None

    async def anchor(payload, ts):
        calls["anchor"].append(payload)
        return FAKE_TX

    def onchain(did):
        calls["erc8004"].append(did)
        return {"agent_id": 4242, "tx_hash": FAKE_TX, "chain": "base"}

    monkeypatch.setattr(m, "db_pool", pool)
    monkeypatch.setattr(m, "anchor_to_base", anchor)
    monkeypatch.setattr(erc8004, "register_onchain_agent", onchain)
    monkeypatch.setattr(m, "check_registration_rate", lambda *a, **k: None)
    monkeypatch.setattr(m, "issue_credential",
                        lambda did, typ, claims: {"issuer": "did:web:api.moltrust.ch"})
    monkeypatch.setattr(m, "vc_valid_from", lambda vc: "2026-10-20T00:00:00Z")
    monkeypatch.setattr(m, "vc_valid_until", lambda vc: "2027-10-20T00:00:00Z")
    monkeypatch.setattr(m, "get_primary_proof_value", lambda vc: "z")
    monkeypatch.setattr(m, "link_api_key_to_did", none)
    monkeypatch.setattr(m, "ensure_balance_row", none)
    monkeypatch.setattr(m, "grant_credits", none)
    monkeypatch.setattr(m, "verify_challenge", lambda c: (True, None))
    monkeypatch.setattr(m, "verify_pow", lambda s, n: (True, None))
    monkeypatch.setattr(m, "verify_pop", lambda k, c, s: (True, None))
    monkeypatch.setattr(accounts, "payer_ref_for_key", none)
    monkeypatch.setattr(agent_profile, "store_declared", none)
    monkeypatch.setattr(agent_profile, "enrich_in_background", none)
    monkeypatch.setattr(referral, "refresh_quietly", none)
    monkeypatch.setattr(m.limiter, "enabled", False)
    m.app.dependency_overrides[m.verify_api_key] = lambda: "mt_test_key"
    try:
        yield pool, calls, TestClient(m.app)
    finally:
        m.app.dependency_overrides.pop(m.verify_api_key, None)


def _register(client, platform, **extra):
    return client.post("/identity/register", json={
        "display_name": f"ta-{uuid.uuid4().hex[:6]}", "platform": platform, **extra})


def _register_pop(client, platform):
    return client.post("/identity/register-pop", json={
        "public_key": uuid.uuid4().hex + uuid.uuid4().hex,
        "challenge": "c", "signature": "s", "pow_nonce": "0",
        "display_name": f"ta-{uuid.uuid4().hex[:6]}", "platform": platform})


def _erc8004_register(client, platform):
    return client.post("/identity/erc8004/register", json={
        "name": f"ta-{uuid.uuid4().hex[:6]}", "platform": platform,
        "wallet_address": "0x" + "1" * 40})


# --- routes: platform=test ---------------------------------------------------

# The register models reject whitespace in `platform`; case still varies.
@pytest.mark.parametrize("platform", ["test", "TEST"])
def test_register_with_platform_test_does_not_anchor(env, platform):
    pool, calls, client = env
    r = _register(client, platform, erc8004=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls == {"anchor": [], "erc8004": []}
    assert body["base_anchor"]["tx_hash"] is None
    assert body["base_anchor"]["explorer"] is None
    assert body["base_anchor"]["status"] == "skipped: platform=test"
    assert body["erc8004"]["status"] == "skipped: platform=test"
    assert body["erc8004"]["agent_id"] is None
    assert pool.base_tx_hash == {} and pool.erc8004_id == {}


def test_register_pop_with_platform_test_does_not_anchor(env):
    pool, calls, client = env
    r = _register_pop(client, "test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls["anchor"] == []
    assert body["base_anchor"]["tx_hash"] is None
    assert body["base_anchor"]["status"] == "skipped: platform=test"
    assert pool.base_tx_hash == {}


def test_erc8004_register_with_platform_test_does_not_anchor(env):
    pool, calls, client = env
    # ERC8004RegisterRequest has no platform validator, so padding arrives.
    r = _erc8004_register(client, " Test ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert calls == {"anchor": [], "erc8004": []}
    assert body["base_tx"] is None
    assert body["base_anchor_status"] == "skipped: platform=test"
    assert body["erc8004_agent_id"] is None
    assert pool.base_tx_hash == {} and pool.erc8004_id == {}


# --- routes: any other platform still anchors --------------------------------

def test_register_with_other_platform_anchors(env):
    pool, calls, client = env
    r = _register(client, "a2a", erc8004=True)
    assert r.status_code == 200, r.text
    body = r.json()
    did = body["did"]
    assert calls == {"anchor": [did], "erc8004": [did]}
    assert body["base_anchor"]["tx_hash"] == FAKE_TX
    assert body["base_anchor"]["status"] == "anchored"
    assert pool.base_tx_hash == {did: FAKE_TX}
    assert pool.erc8004_id == {did: 4242}


def test_register_pop_with_other_platform_anchors(env):
    pool, calls, client = env
    r = _register_pop(client, "a2a")
    assert r.status_code == 200, r.text
    did = r.json()["did"]
    assert calls["anchor"] == [did]
    assert r.json()["base_anchor"]["status"] == "anchored"
    assert pool.base_tx_hash == {did: FAKE_TX}


def test_erc8004_register_with_other_platform_anchors(env):
    pool, calls, client = env
    r = _erc8004_register(client, "base")
    assert r.status_code == 200, r.text
    did = r.json()["moltrust_did"]
    assert calls == {"anchor": [did], "erc8004": [did]}
    assert r.json()["base_anchor_status"] == "anchored"


def test_a_failed_anchor_is_reported_as_not_anchored(env, monkeypatch):
    pool, calls, client = env

    async def failed(payload, ts):
        return None

    monkeypatch.setattr(m, "anchor_to_base", failed)
    r = _register(client, "a2a")
    assert r.status_code == 200, r.text
    assert r.json()["base_anchor"] == {"tx_hash": None, "chain": "base",
                                       "status": "not_anchored", "explorer": None}


# --- static: no route reaches the signers around the guard -------------------

SIGNERS = {"anchor_to_base", "register_onchain_agent"}

# Where a direct call is allowed. The two doors, plus the three sports routes,
# which anchor for an agent that already exists and carry no platform in the
# request (see docs/invariants/c-test-platform-never-anchored.yaml).
ALLOWED = {
    "anchor_to_base": {"anchor_registration", "sports_predict_commit",
                       "signal_provider_register", "fantasy_lineup_commit"},
    "register_onchain_agent": {"erc8004_register_for_platform"},
}


def _called_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _route_paths(fn):
    out = []
    for d in fn.decorator_list:
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name) and d.func.value.id == "app"
                and d.args and isinstance(d.args[0], ast.Constant)):
            out.append(d.args[0].value)
    return out


def _functions():
    tree = ast.parse(MAIN.read_text())
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_signers_are_called_only_from_the_allowed_functions():
    seen = {name: set() for name in SIGNERS}
    for fn in _functions():
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _called_name(node) in SIGNERS:
                seen[_called_name(node)].add(fn.name)
    for name in SIGNERS:
        stray = seen[name] - ALLOWED[name]
        assert not stray, f"{name} called from {sorted(stray)}; go through the guard"


def test_every_identity_route_that_anchors_uses_the_guard():
    """Each /identity/* route that reaches the chain does so through a door."""
    doors = {"anchor_registration", "erc8004_register_for_platform"}
    anchoring = {}
    for fn in _functions():
        paths = [p for p in _route_paths(fn) if p.startswith("/identity/")]
        if not paths:
            continue
        names = {_called_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert not (names & SIGNERS), f"{fn.name} ({paths}) calls a signer directly"
        if names & doors:
            anchoring[fn.name] = paths
    # The four register routes that write on chain, by name, so a rename or a
    # removed door is noticed here and not in production.
    assert set(anchoring) == {"register_agent", "register_agent_pop",
                              "register_batch", "erc8004_dual_register"}, anchoring
