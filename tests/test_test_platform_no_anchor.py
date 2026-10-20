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


# --- static: nothing reaches the productive signer around a guard -------------

APP = MAIN.parent

# Every function in app/ that signs a Base transaction, and with which key.
# A new signing function fails test_signer_inventory_is_complete until it is
# listed here, and if it signs with the productive key, until it is in SIGNERS
# and has a door.
SIGNER_INVENTORY = {
    ("main.py", "anchor_to_base"): "BASE_WRITE_KEY",
    ("main.py", "_anchor_music_vc"): "BASE_WRITE_KEY",
    ("provenance/anchor.py", "anchor_single_calldata"): "BASE_WRITE_KEY",
    ("erc8004.py", "post_reputation_feedback"): "BASE_WRITE_KEY",
    ("erc8004.py", "register_onchain_agent"): "BASE_WRITE_KEY",
    ("erc8004_reputation.py", "publish_verdict"): "BASE_WRITE_KEY",
    # Test wallet 0xd8f5 (vc_anchor_signer), not the productive key: the
    # credential batch of /credentials/admin/anchor and scripts/anchor_iprs.sh.
    ("provenance/anchor.py", "anchor_calldata_from_anchor_wallet"): "BASE_ANCHOR_KEY",
}

SIGNERS = {fn for (_, fn), key in SIGNER_INVENTORY.items() if key == "BASE_WRITE_KEY"}

# Where a reference to a productive signer is allowed: the doors in app/main.py,
# which skip platform=test, and three named exceptions.
ALLOWED = {
    "anchor_to_base": {
        "anchor_registration", "anchor_for_agents",
        # app/swarm/interaction_proof.py passes one argument to a two-argument
        # function: TypeError before anything is signed, every time. Pinned by
        # test_interaction_proof_anchor_never_reaches_the_signer; whoever fixes
        # the arity has to route it through anchor_for_agents (see PR #610).
        "anchor_on_chain",
    },
    "anchor_single_calldata": {
        "anchor_calldata_for_agents",
        # Admin key only (cron scripts/anchor_iprs.sh). The function it hands the
        # signer to, anchor_batch, leaves test agents' records out of the batch
        # (tests/test_test_agent_no_anchor.py, against the database).
        "ipr_admin_anchor",
    },
    "register_onchain_agent": {"erc8004_register_for_platform"},
    "post_reputation_feedback": {"reputation_feedback_for_agents"},
    "_anchor_music_vc": {"schedule_music_anchor"},
    # No caller in app/ (tests and a planned script only).
    "publish_verdict": set(),
}

DOORS = {"anchor_registration", "erc8004_register_for_platform", "anchor_for_agents",
         "anchor_calldata_for_agents", "reputation_feedback_for_agents",
         "schedule_music_anchor"}


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


def _trees():
    for path in sorted(APP.rglob("*.py")):
        yield path.relative_to(APP).as_posix(), ast.parse(path.read_text())


def _functions(tree=None):
    tree = tree or ast.parse(MAIN.read_text())
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _references(node):
    """Names a node refers to: calls, plain loads, attribute loads, imports."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            yield n.id
        elif isinstance(n, ast.Attribute):
            yield n.attr
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                yield a.name


def _signs(fn):
    """Which key family a function signs with, if it signs at all."""
    names = set(_references(fn))
    if "vc_anchor_signer" in names:
        return "BASE_ANCHOR_KEY"
    if names & {"ipr_anchor_signer", "registry_write_signer", "_WRITE_KEY"}:
        return "BASE_WRITE_KEY"
    if "sign_transaction" in names:
        return "unknown key"
    return None


def test_signer_inventory_is_complete():
    found = {}
    for rel, tree in _trees():
        for fn in _functions(tree):
            key = _signs(fn)
            # A nested helper is attributed to its outer function too; only the
            # innermost one that signs is the signer.
            if key and not any(_signs(inner) for inner in _functions(fn) if inner is not fn):
                found[(rel, fn.name)] = key
    assert found == SIGNER_INVENTORY


def test_signers_are_referenced_only_from_the_allowed_functions():
    seen = {name: set() for name in SIGNERS}
    for rel, tree in _trees():
        for fn in _functions(tree):
            for name in set(_references(fn)) & SIGNERS:
                if name != fn.name:
                    seen[name].add(fn.name)
    for name in SIGNERS:
        stray = seen[name] - ALLOWED[name]
        assert not stray, f"{name} referenced from {sorted(stray)}; go through a door"


def test_no_signer_is_imported_under_another_name():
    """An alias would hide a caller from the scan above."""
    for rel, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    assert not (a.name in SIGNERS and a.asname), (
                        f"{rel}: {a.name} imported as {a.asname}")


def test_every_identity_route_that_anchors_uses_the_guard():
    """Each /identity/* route that reaches the chain does so through a door."""
    anchoring = {}
    for fn in _functions():
        paths = [p for p in _route_paths(fn) if p.startswith("/identity/")]
        if not paths:
            continue
        names = {_called_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert not (names & SIGNERS), f"{fn.name} ({paths}) calls a signer directly"
        if names & DOORS:
            anchoring[fn.name] = paths
    # The routes that write on chain, by name, so a rename or a removed door is
    # noticed here and not in production.
    assert set(anchoring) == {"register_agent", "register_agent_pop",
                              "register_batch", "erc8004_dual_register",
                              "erc8004_validate"}, anchoring


def test_every_route_that_reaches_a_door_is_known():
    """All routes, not only /identity/*: the list of routes that can sign."""
    routes = {}
    for fn in _functions():
        if not _route_paths(fn):
            continue
        names = {_called_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert not (names & SIGNERS), f"{fn.name} calls a signer directly"
        if names & DOORS:
            routes[fn.name] = sorted(names & DOORS)
    assert routes == {
        "register_agent": ["anchor_registration", "erc8004_register_for_platform"],
        "register_agent_pop": ["anchor_registration"],
        "register_batch": ["anchor_registration"],
        "erc8004_dual_register": ["anchor_registration", "erc8004_register_for_platform"],
        "erc8004_validate": ["reputation_feedback_for_agents"],
        "rate_agent": ["reputation_feedback_for_agents"],
        "sports_predict_commit": ["anchor_for_agents"],
        "signal_provider_register": ["anchor_for_agents"],
        "fantasy_lineup_commit": ["anchor_for_agents"],
        "anchors_batch": ["anchor_calldata_for_agents"],
        "issue_music_credential": ["schedule_music_anchor"],
    }, routes


def test_interaction_proof_anchor_never_reaches_the_signer(monkeypatch):
    """The one listed exception outside the doors signs nothing today."""
    import asyncio

    from app.swarm import interaction_proof

    reached = []

    def signer():
        reached.append("signer")
        raise RuntimeError("stop")

    async def slot():
        reached.append("budget")
        return False, 0

    monkeypatch.setattr(m, "ipr_anchor_signer", signer)
    monkeypatch.setattr(m, "_claim_anchor_slot", slot)
    asyncio.run(interaction_proof.anchor_on_chain("ab" * 32))
    assert reached == []


def test_anchor_existing_keys_script_leaves_test_agents_out():
    """scripts/anchor_existing_keys.py signs with the productive key per agent."""
    tree = ast.parse((APP.parent / "scripts" / "anchor_existing_keys.py").read_text())
    selects = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and n.value.startswith("SELECT") and "FROM agents" in n.value]
    assert len(selects) == 1, selects
    assert "AND lower(btrim(coalesce(platform, ''))) <> 'test'" in selects[0]
