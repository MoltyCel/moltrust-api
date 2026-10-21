"""Who may make the registry sign, or the productive key send (app/sign_guard.py).

Four routes sign a credential or start a transaction from the productive key:

- POST /credentials/issue         -> issue_credential (registry signature)
- POST /music/credential/issue    -> _anchor_music_vc (cast send)
- POST /anchors/batch             -> anchor_single_calldata (submit_onchain)
- POST /identity/erc8004/validate -> issue_credential + post_reputation_feedback

The first two act for a named DID, so the key has to own it (or the caller is
admin). The last two have no DID of the caller in the body; the validator
subject is a third party by design. For them the key has to belong to an agent.
Every refusal happens before anything is signed, sent or written.

The first part runs the routes against a stand-in pool with the signer and the
senders replaced by recorders, and the credit middleware off, so the route's
own check is what is measured. The second part runs against the sandbox
database: a refused issuance leaves no row and costs no credit, and a lookup of
a revoked DID on /x402/verify is logged again (#882 had dropped it).
"""
import contextlib
import datetime
import hashlib
import logging
import os
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import erc8004, sign_guard

FAKE_TX = "0x" + "cd" * 32
OWN_DID = "did:moltrust:" + "a2a0" * 4
FOREIGN_DID = "did:moltrust:" + "0b0b" * 4
KEY = "mt_test_key"
ADMIN = "test-admin-key-for-sign-guard"


# --- stand-in pool -----------------------------------------------------------

class Conn:
    def __init__(self, pool):
        self.pool = pool

    async def fetchval(self, sql, *args):
        self.pool.reads.append(sql)
        if "FROM api_keys" in sql:
            return self.pool.caller
        if sql.startswith("SELECT platform FROM agents WHERE did"):
            return "a2a"
        return None

    async def fetchrow(self, sql, *args):
        self.pool.reads.append(sql)
        if sql.startswith("SELECT did FROM agents WHERE erc8004_agent_id"):
            return {"did": FOREIGN_DID}
        if "AVG(score)" in sql:
            return {"avg_score": 4.0, "avg": 4.0, "total": 1}
        return None

    async def fetch(self, sql, *args):
        return []

    async def execute(self, sql, *args):
        # request_log is the access log of the middleware, written for every
        # request whatever the route decides; not a side effect of the route.
        if not sql.startswith("INSERT INTO request_log"):
            self.pool.writes.append(sql.split("(")[0].strip())
        return "OK"

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self):
        self.caller = OWN_DID
        self.reads = []
        self.writes = []

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield Conn(self)


@pytest.fixture
def env(monkeypatch):
    pool = Pool()
    calls = {"sign": [], "calldata": [], "music": [], "feedback": [], "onchain": []}
    real_issue = m.issue_credential

    def sign(did, typ, claims):
        calls["sign"].append((did, typ))
        return real_issue(did, typ, claims)

    async def calldata(data):
        calls["calldata"].append(data)
        return FAKE_TX

    async def _noop():
        return None

    def music(track_hash, credential_id):
        calls["music"].append(track_hash)
        return _noop()

    def feedback(agent_id, did, score):
        calls["feedback"].append((agent_id, did))
        return {"tx_hash": FAKE_TX, "chain": "base"}

    async def onchain(agent_id):
        calls["onchain"].append(agent_id)
        return {"owner": "0x" + "1" * 40}

    # Signing keys made for this process: no output carries a real signature.
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    monkeypatch.delenv("DID_PRIVATE_KEY_ENCRYPTED", raising=False)
    monkeypatch.setenv("ADMIN_KEY", ADMIN)
    monkeypatch.setattr(m, "db_pool", pool)
    monkeypatch.setattr(m, "CREDITS_ENABLED", False)
    monkeypatch.setattr(m, "issue_credential", sign)
    monkeypatch.setattr(m, "publish_to_ipfs", lambda doc: None)
    monkeypatch.setattr(m, "anchor_single_calldata", calldata)
    monkeypatch.setattr(m, "_anchor_music_vc", music)
    monkeypatch.setattr(erc8004, "post_reputation_feedback", feedback)
    monkeypatch.setattr(m, "resolve_onchain_agent", onchain)
    monkeypatch.setattr(m.limiter, "enabled", False)
    m.app.dependency_overrides[m.verify_api_key] = lambda: KEY
    try:
        yield pool, calls, TestClient(m.app)
    finally:
        m.app.dependency_overrides.pop(m.verify_api_key, None)


def _issue(client, did, headers=None):
    return client.post("/credentials/issue", json={"subject_did": did}, headers=headers or {})


def _music(client, did, headers=None):
    return client.post("/music/credential/issue", json={
        "agent_did": did, "tool": "t", "human_oversight": "true",
        "rights": "cc-by", "track_title": "ta"}, headers=headers or {})


def _batch(client, submit=True):
    return client.post("/anchors/batch", json={"hashes": ["ab" * 32, "cd" * 32],
                                               "submit_onchain": submit})


def _validate(client):
    return client.post("/identity/erc8004/validate", json={"erc8004_agent_id": 77})


def _inserts(pool, table):
    return [w for w in pool.writes if w.startswith(f"INSERT INTO {table}")]


# --- /credentials/issue --------------------------------------------------------

def test_issue_for_own_did_signs(env):
    pool, calls, client = env
    r = _issue(client, OWN_DID)
    assert r.status_code == 200, r.text
    assert calls["sign"] == [(OWN_DID, "AgentTrustCredential")]
    assert r.json()["credentialSubject"]["id"] == OWN_DID
    assert _inserts(pool, "credentials")


def test_issue_for_foreign_did_is_refused_before_signing(env):
    pool, calls, client = env
    r = _issue(client, FOREIGN_DID)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == sign_guard.NOT_OWNER_DETAIL
    assert calls["sign"] == []
    assert pool.writes == []


def test_issue_with_key_without_agent_is_refused_before_signing(env):
    pool, calls, client = env
    pool.caller = None
    r = _issue(client, OWN_DID)
    assert r.status_code == 403, r.text
    assert calls["sign"] == []
    assert pool.writes == []


def test_issue_admin_may_issue_for_any_did(env):
    pool, calls, client = env
    pool.caller = None
    r = _issue(client, FOREIGN_DID, headers={"X-Admin-Key": ADMIN})
    assert r.status_code == 200, r.text
    assert calls["sign"] == [(FOREIGN_DID, "AgentTrustCredential")]


def test_issue_wrong_admin_key_does_not_bypass(env):
    pool, calls, client = env
    r = _issue(client, FOREIGN_DID, headers={"X-Admin-Key": ADMIN + "x"})
    assert r.status_code == 403, r.text
    assert calls["sign"] == []


# --- /music/credential/issue ---------------------------------------------------

def test_music_for_own_did_anchors(env):
    pool, calls, client = env
    r = _music(client, OWN_DID)
    assert r.status_code == 200, r.text
    assert len(calls["music"]) == 1
    assert _inserts(pool, "music_credentials")


def test_music_for_foreign_did_is_refused_before_anchor(env):
    pool, calls, client = env
    r = _music(client, FOREIGN_DID)
    assert r.status_code == 403, r.text
    assert calls["music"] == []
    assert pool.writes == []


def test_music_with_key_without_agent_is_refused_before_anchor(env):
    pool, calls, client = env
    pool.caller = None
    r = _music(client, OWN_DID)
    assert r.status_code == 403, r.text
    assert calls["music"] == []
    assert pool.writes == []


def test_music_admin_may_issue_for_any_did(env):
    pool, calls, client = env
    pool.caller = None
    r = _music(client, FOREIGN_DID, headers={"X-Admin-Key": ADMIN})
    assert r.status_code == 200, r.text
    assert _inserts(pool, "music_credentials")


# --- /anchors/batch ------------------------------------------------------------

def test_anchors_batch_with_agent_key_sends(env):
    pool, calls, client = env
    r = _batch(client)
    assert r.status_code == 200, r.text
    assert calls["calldata"] == [f"MolTrust/anchors/v1/{r.json()['merkle_root']}"]
    assert r.json()["tx_hash"] == FAKE_TX


def test_anchors_batch_with_key_without_agent_is_refused_before_send(env):
    pool, calls, client = env
    pool.caller = None
    r = _batch(client)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == sign_guard.NO_AGENT_DETAIL
    assert calls["calldata"] == []
    assert pool.writes == []


def test_anchors_batch_without_submit_needs_no_agent(env):
    """Computing a Merkle root signs nothing; the check is on the send."""
    pool, calls, client = env
    pool.caller = None
    r = _batch(client, submit=False)
    assert r.status_code == 200, r.text
    assert r.json()["anchor_status"] == "computed"
    assert calls["calldata"] == []


# --- /identity/erc8004/validate ------------------------------------------------

def test_erc8004_validate_with_agent_key_signs_and_sends(env):
    """The subject is another agent: a validator validates a third party."""
    pool, calls, client = env
    r = _validate(client)
    assert r.status_code == 200, r.text
    assert calls["sign"] == [(FOREIGN_DID, "AgentValidationCredential")]
    assert calls["feedback"] == [(77, FOREIGN_DID)]
    assert r.json()["on_chain_tx"] == FAKE_TX


def test_erc8004_validate_with_key_without_agent_is_refused_before_anything(env):
    pool, calls, client = env
    pool.caller = None
    r = _validate(client)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == sign_guard.NO_AGENT_DETAIL
    assert calls["sign"] == []
    assert calls["feedback"] == []
    assert calls["onchain"] == []
    assert pool.writes == []


# --- one ownership rule --------------------------------------------------------

async def test_compliance_helper_is_the_shared_one(env, monkeypatch):
    """_require_did_owner_or_admin delegates, so ownership cannot drift apart."""
    seen = []

    async def spy(request, pool, api_key, subject_did):
        seen.append((pool, api_key, subject_did))

    monkeypatch.setattr(sign_guard, "require_did_owner_or_admin", spy)
    await m._require_did_owner_or_admin(object(), KEY, OWN_DID)
    assert seen == [(m.db_pool, KEY, OWN_DID)]


# --- sandbox database ----------------------------------------------------------

@pytest.fixture
def _db_keys(monkeypatch):
    monkeypatch.setenv("DID_PRIVATE_KEY_HEX", os.urandom(32).hex())
    monkeypatch.delenv("DID_PRIVATE_KEY_ENCRYPTED", raising=False)
    monkeypatch.setattr(m, "publish_to_ipfs", lambda doc: None)
    previous = m.limiter.enabled
    m.limiter.enabled = False
    yield
    m.limiter.enabled = previous


async def test_db_issue_for_foreign_did_writes_nothing_and_costs_nothing(
        async_client, credit_test_agent, _db_keys):
    caller, key = await credit_test_agent(balance=1000)
    victim, _ = await credit_test_agent(balance=1000)
    r = await async_client.post("/credentials/issue", json={"subject_did": victim},
                                headers={"X-API-Key": key})
    assert r.status_code == 403, r.text
    async with m.db_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM credentials WHERE subject_did = $1", victim) == 0
        assert await conn.fetchval(
            "SELECT balance FROM credit_balances WHERE did = $1", caller) == 1000


async def test_db_issue_for_own_did_still_works(async_client, credit_test_agent, _db_keys):
    did, key = await credit_test_agent(balance=1000)
    r = await async_client.post("/credentials/issue", json={"subject_did": did},
                                headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    async with m.db_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM credentials WHERE subject_did = $1", did) == 1


# --- /x402/verify: a revoked lookup is logged again ------------------------------

@pytest.fixture
async def revoked_did(app_with_lifespan):
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    async with m.db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, agent_type, revoked_at, "
            "revocation_reason) VALUES ($1, $2, 'test', 'external', now(), 'test: revoked')",
            did, f"sg-{did[-8:]}")
    yield did
    async with m.db_pool.acquire() as conn:
        await conn.execute("DELETE FROM x402_verify_calls WHERE queried_did = $1", did)
        await conn.execute("DELETE FROM agents WHERE did = $1", did)


async def test_x402_verify_revoked_is_logged_by_hash(async_client, revoked_did, caplog):
    previous = m.limiter.enabled
    m.limiter.enabled = False
    before = datetime.datetime.utcnow()
    try:
        with caplog.at_level(logging.INFO, logger="moltrust"):
            r = await async_client.get("/x402/verify", params={"did": revoked_did})
    finally:
        m.limiter.enabled = previous
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is False and body["payment_ready"] is False
    assert body["reason"] == "revoked"

    async with m.db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT result_payment_ready, result_trust_score, called_at FROM x402_verify_calls "
            "WHERE queried_did = $1", revoked_did)
    assert len(rows) == 1
    assert rows[0]["result_payment_ready"] is False
    assert rows[0]["result_trust_score"] is None
    assert rows[0]["called_at"] >= before - datetime.timedelta(seconds=1)

    lines = [rec.getMessage() for rec in caplog.records if "x402/verify revoked" in rec.getMessage()]
    assert len(lines) == 1
    digest = hashlib.sha256(revoked_did.encode()).hexdigest()[:12]
    assert f"did_sha256={digest}" in lines[0]
    assert revoked_did not in lines[0]
    assert revoked_did.split(":")[-1] not in lines[0]
