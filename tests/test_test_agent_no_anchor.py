"""An agent registered with platform=test never makes the productive key sign.

tests/test_test_platform_no_anchor.py covers the register routes, where the
platform arrives in the request. This file covers the routes that act for an
agent that already exists, where the platform has to be looked up
(app/anchor_guard.py any_test_agent):

- /sports/predictions/commit, /sports/signals/register,
  /sports/fantasy/lineups/commit  -> anchor_to_base
- /anchors/batch (submit_onchain)  -> anchor_single_calldata
- /music/credential/issue          -> _anchor_music_vc (cast send)
- /reputation/rate, /identity/erc8004/validate -> post_reputation_feedback
- /vc/ipr/admin/anchor (cron)      -> anchor_batch leaves test records out

Each route twice: with a test agent (signer replaced by a recorder, never
called, status says why) and with another agent (called once). The routes run
against a stand-in pool; anchor_batch runs against the test database, because
its exclusion is SQL and only Postgres can say whether it holds.
"""
import contextlib
import datetime
import os
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import anchor_guard, erc8004

FAKE_TX = "0x" + "cd" * 32
SKIPPED = "skipped: platform=test"

TEST_DID = "did:moltrust:" + "7e57" * 4
REAL_DID = "did:moltrust:" + "a2a0" * 4
OTHER_DID = "did:moltrust:" + "0b0b" * 4
PLATFORMS = {TEST_DID: "test", REAL_DID: "a2a", OTHER_DID: "moltbook"}
KEY = "mt_test_key"


# --- the helper --------------------------------------------------------------

class PlatformConn:
    def __init__(self, platforms):
        self.platforms = platforms
        self.asked = []

    async def fetchval(self, sql, *args):
        assert sql == "SELECT platform FROM agents WHERE did = $1"
        self.asked.append(args[0])
        return self.platforms.get(args[0])


@pytest.mark.parametrize("platform", ["test", "TEST", " Test "])
async def test_is_test_agent_reads_the_platform(platform):
    assert await anchor_guard.is_test_agent(PlatformConn({TEST_DID: platform}), TEST_DID)


@pytest.mark.parametrize("did", [REAL_DID, OTHER_DID, "did:moltrust:ffffffffffffffff", None, ""])
async def test_is_test_agent_is_false_for_others_and_unknown(did):
    assert not await anchor_guard.is_test_agent(PlatformConn(PLATFORMS), did)


async def test_any_test_agent_needs_one_test_agent():
    conn = PlatformConn(PLATFORMS)
    assert await anchor_guard.any_test_agent(conn, [REAL_DID, None, TEST_DID])
    assert not await anchor_guard.any_test_agent(conn, [REAL_DID, OTHER_DID, None])
    assert not await anchor_guard.any_test_agent(conn, [])


async def test_any_test_agent_asks_once_per_did():
    conn = PlatformConn(PLATFORMS)
    await anchor_guard.any_test_agent(conn, [REAL_DID, REAL_DID, OTHER_DID])
    assert conn.asked == [REAL_DID, OTHER_DID]


# --- stand-in pool -----------------------------------------------------------

class Conn:
    def __init__(self, db):
        self.db = db

    async def fetchval(self, sql, *args):
        if sql.startswith("SELECT platform FROM agents WHERE did"):
            return PLATFORMS.get(args[0])
        if "FROM api_keys" in sql:
            return self.db.caller
        if sql.startswith("SELECT 1 FROM agents WHERE did"):
            return 1 if args[0] in PLATFORMS else None
        return None

    async def fetchrow(self, sql, *args):
        if sql.startswith("SELECT erc8004_agent_id FROM agents"):
            return {"erc8004_agent_id": 77}
        if sql.startswith("SELECT did FROM agents WHERE erc8004_agent_id"):
            return {"did": self.db.subject}
        if "AVG(score)" in sql:
            return {"avg_score": 4.0, "total": 1}
        return None

    async def fetch(self, sql, *args):
        return []

    async def execute(self, sql, *args):
        return "OK"

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self):
        self.caller = REAL_DID
        self.subject = OTHER_DID

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield Conn(self)


@pytest.fixture
def env(monkeypatch):
    pool = Pool()
    calls = {"anchor": [], "calldata": [], "music": [], "feedback": []}

    async def anchor(payload, ts):
        calls["anchor"].append(payload)
        return FAKE_TX

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

    async def none(*a, **k):
        return None

    async def row(*a, **k):
        return {"created_at": datetime.datetime.now(datetime.timezone.utc)}

    async def onchain(agent_id):
        return {"owner": "0x" + "1" * 40}

    monkeypatch.setattr(m, "db_pool", pool)
    monkeypatch.setattr(m, "anchor_to_base", anchor)
    monkeypatch.setattr(m, "anchor_single_calldata", calldata)
    monkeypatch.setattr(m, "_anchor_music_vc", music)
    monkeypatch.setattr(erc8004, "post_reputation_feedback", feedback)
    monkeypatch.setattr(m, "insert_prediction", row)
    monkeypatch.setattr(m, "get_provider_by_did", none)
    monkeypatch.setattr(m, "insert_provider", row)
    monkeypatch.setattr(m, "insert_lineup", row)
    monkeypatch.setattr(m, "issue_fantasy_lineup_credential", lambda did, claims: {})
    monkeypatch.setattr(m, "resolve_onchain_agent", onchain)
    monkeypatch.setattr(m, "issue_credential", lambda did, typ, claims: {})
    monkeypatch.setattr(m.limiter, "enabled", False)
    m.app.dependency_overrides[m.verify_api_key] = lambda: KEY
    try:
        yield pool, calls, TestClient(m.app)
    finally:
        m.app.dependency_overrides.pop(m.verify_api_key, None)


def _future(days=3):
    dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    return dt.isoformat().replace("+00:00", "Z")


def _predict(client, did):
    return client.post("/sports/predictions/commit", json={
        "agent_did": did, "event_id": f"evt-{uuid.uuid4().hex[:10]}",
        "prediction": {"winner": "home"}, "event_start": _future()})


def _signal(client, did):
    return client.post("/sports/signals/register", json={
        "agent_did": did, "provider_name": "ta"})


def _lineup(client, did):
    return client.post("/sports/fantasy/lineups/commit", json={
        "agent_did": did, "contest_id": f"c-{uuid.uuid4().hex[:8]}",
        "platform": sorted(m.VALID_PLATFORMS)[0], "sport": sorted(m.VALID_SPORTS)[0],
        "contest_start_iso": _future(), "lineup": {"qb": "x"}})


SPORTS = {
    "predictions": (_predict, lambda b: b["base_tx_hash"], lambda b: b["anchor_status"]),
    "signals": (_signal, lambda b: b["credential"]["tx_hash"],
                lambda b: b["credential"]["anchor_status"]),
    "fantasy": (_lineup, lambda b: b["tx_hash"], lambda b: b["anchor_status"]),
}


# --- sports: anchor_to_base ----------------------------------------------------

@pytest.mark.parametrize("route", sorted(SPORTS))
def test_sports_route_for_a_test_agent_does_not_anchor(env, route):
    pool, calls, client = env
    pool.caller = TEST_DID
    send, tx, status = SPORTS[route]
    r = send(client, TEST_DID)
    assert r.status_code in (200, 201), r.text
    assert calls["anchor"] == []
    assert tx(r.json()) is None
    assert status(r.json()) == SKIPPED


@pytest.mark.parametrize("route", sorted(SPORTS))
def test_sports_route_for_another_agent_anchors(env, route):
    pool, calls, client = env
    pool.caller = REAL_DID
    send, tx, status = SPORTS[route]
    r = send(client, REAL_DID)
    assert r.status_code in (200, 201), r.text
    assert len(calls["anchor"]) == 1
    assert tx(r.json()) == FAKE_TX
    assert status(r.json()) == "anchored"


# --- /anchors/batch: anchor_single_calldata --------------------------------------

def _batch(client):
    return client.post("/anchors/batch", json={
        "hashes": ["ab" * 32, "cd" * 32], "submit_onchain": True})


def test_anchors_batch_for_a_test_agent_does_not_anchor(env):
    pool, calls, client = env
    pool.caller = TEST_DID
    r = _batch(client)
    assert r.status_code == 200, r.text
    assert calls["calldata"] == []
    assert r.json()["tx_hash"] is None
    assert r.json()["anchor_status"] == SKIPPED
    assert r.json()["merkle_root"] and len(r.json()["proofs"]) == 2


def test_anchors_batch_for_another_agent_anchors(env):
    pool, calls, client = env
    pool.caller = REAL_DID
    r = _batch(client)
    assert r.status_code == 200, r.text
    assert calls["calldata"] == [f"MolTrust/anchors/v1/{r.json()['merkle_root']}"]
    assert r.json()["tx_hash"] == FAKE_TX
    assert r.json()["anchor_status"] == "anchored"


def test_anchors_batch_without_submit_does_not_look_anything_up(env):
    pool, calls, client = env
    r = client.post("/anchors/batch", json={"hashes": ["ab" * 32]})
    assert r.status_code == 200, r.text
    assert r.json()["anchor_status"] == "computed"
    assert calls["calldata"] == []


# --- /music/credential/issue: _anchor_music_vc -----------------------------------

def _music(client, did):
    return client.post("/music/credential/issue", json={
        "agent_did": did, "tool": "t", "human_oversight": "true",
        "rights": "cc-by", "track_title": "ta"})


@pytest.mark.parametrize("caller,agent", [(TEST_DID, TEST_DID), (TEST_DID, REAL_DID),
                                          (REAL_DID, TEST_DID)])
def test_music_credential_with_a_test_agent_does_not_anchor(env, caller, agent):
    pool, calls, client = env
    pool.caller = caller
    r = _music(client, agent)
    assert r.status_code == 200, r.text
    assert calls["music"] == []
    assert r.json()["anchor"]["status"] == SKIPPED


def test_music_credential_for_another_agent_anchors(env):
    pool, calls, client = env
    pool.caller = REAL_DID
    r = _music(client, REAL_DID)
    assert r.status_code == 200, r.text
    assert len(calls["music"]) == 1
    assert "status" not in r.json()["anchor"]


# --- /reputation/rate and /identity/erc8004/validate: giveFeedback ---------------

def _rate(client, frm, to):
    return client.post("/reputation/rate", json={"from_did": frm, "to_did": to, "score": 4})


@pytest.mark.parametrize("frm,to", [(TEST_DID, REAL_DID), (REAL_DID, TEST_DID)])
def test_rating_with_a_test_agent_posts_no_feedback(env, frm, to):
    pool, calls, client = env
    pool.caller = frm
    r = _rate(client, frm, to)
    assert r.status_code == 200, r.text
    assert calls["feedback"] == []
    assert r.json()["erc8004_tx"] is None
    assert r.json()["erc8004_status"] == SKIPPED


def test_rating_between_other_agents_posts_feedback(env):
    pool, calls, client = env
    pool.caller = REAL_DID
    r = _rate(client, REAL_DID, OTHER_DID)
    assert r.status_code == 200, r.text
    assert calls["feedback"] == [(77, OTHER_DID)]
    assert r.json()["erc8004_tx"] == FAKE_TX
    assert "erc8004_status" not in r.json()


def _validate(client):
    return client.post("/identity/erc8004/validate", json={"erc8004_agent_id": 77})


@pytest.mark.parametrize("caller,subject", [(TEST_DID, REAL_DID), (REAL_DID, TEST_DID)])
def test_erc8004_validate_with_a_test_agent_posts_no_feedback(env, caller, subject):
    pool, calls, client = env
    pool.caller, pool.subject = caller, subject
    r = _validate(client)
    assert r.status_code == 200, r.text
    assert calls["feedback"] == []
    assert r.json()["on_chain_tx"] is None
    assert r.json()["on_chain_status"] == SKIPPED


def test_erc8004_validate_for_other_agents_posts_feedback(env):
    pool, calls, client = env
    pool.caller, pool.subject = REAL_DID, OTHER_DID
    r = _validate(client)
    assert r.status_code == 200, r.text
    assert calls["feedback"] == [(77, OTHER_DID)]
    assert r.json()["on_chain_tx"] == FAKE_TX


# --- anchor_batch (cron via /vc/ipr/admin/anchor): real database ------------------

class _NoChain:
    """Stands in for web3.Web3 inside anchor_batch, so no receipt is fetched."""

    def __init__(self, *a, **k):
        raise RuntimeError("no chain in tests")

    @staticmethod
    def HTTPProvider(*a, **k):
        raise RuntimeError("no chain in tests")


@pytest.fixture
async def ipr_conn(monkeypatch):
    import web3
    monkeypatch.setattr(web3, "Web3", _NoChain)
    conn = await asyncpg.connect(host="localhost", database=os.getenv("DB_NAME", "moltstack"),
                                 user="moltstack")
    tr = conn.transaction()
    await tr.start()
    # Only this test's records are pending inside the transaction.
    await conn.execute("UPDATE interaction_proof_records SET anchor_status = 'failed' "
                       "WHERE anchor_status = 'pending'")
    try:
        yield conn
    finally:
        await tr.rollback()
        await conn.close()


async def _agent_with_ipr(conn, platform):
    did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    await conn.execute("INSERT INTO agents (did, display_name, platform, agent_type) "
                       "VALUES ($1, $2, $3, 'external')", did, f"tc-{did[-8:]}", platform)
    rid = await conn.fetchval(
        """INSERT INTO interaction_proof_records
           (agent_did, output_hash, confidence, agent_signature, produced_at)
           VALUES ($1, $2, 0.5, 'sig', now()) RETURNING id""",
        did, "sha256:" + uuid.uuid4().hex + uuid.uuid4().hex)
    return rid


async def _status(conn, rid):
    return await conn.fetchrow(
        "SELECT anchor_status, anchor_tx FROM interaction_proof_records WHERE id = $1", rid)


async def test_ipr_batch_leaves_test_agents_out(ipr_conn):
    from app.provenance.anchor import anchor_batch
    test_rec = await _agent_with_ipr(ipr_conn, " Test ")
    real_rec = await _agent_with_ipr(ipr_conn, "a2a")
    sent = []

    async def anchor_fn(calldata):
        sent.append(calldata)
        return FAKE_TX

    result = await anchor_batch(ipr_conn, anchor_fn)
    assert len(sent) == 1
    assert result["batched"] == 1 and result["status"] == "anchored"
    assert dict(await _status(ipr_conn, real_rec)) == {"anchor_status": "anchored",
                                                       "anchor_tx": FAKE_TX}
    assert dict(await _status(ipr_conn, test_rec)) == {"anchor_status": "pending",
                                                       "anchor_tx": None}


async def test_ipr_batch_of_only_test_agents_sends_nothing(ipr_conn):
    from app.provenance.anchor import anchor_batch
    test_rec = await _agent_with_ipr(ipr_conn, "test")
    sent = []

    async def anchor_fn(calldata):
        sent.append(calldata)
        return FAKE_TX

    result = await anchor_batch(ipr_conn, anchor_fn)
    assert sent == []
    # batched=0 is what ends the loop in scripts/anchor_iprs.sh.
    assert result == {"batched": 0, "status": "no_pending"}
    assert (await _status(ipr_conn, test_rec))["anchor_status"] == "pending"
