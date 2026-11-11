"""POST /identity/register takes a referral like register-pop does (ops KM1).

Until KM1 only the keyless path accepted `referrer_did`, so every agent that
registered with an API key could not have been brought by anyone, and the
cohort figure "mitgebracht" read 0 for a reason that had nothing to do with
the agents.

No database: the pool is a stand-in that answers the handful of queries the
handler asks and records what it writes. What is pinned here is the order —
a refused referral is refused before the agent row exists — and that the
row in agent_referrals is written for a taken one. The same refusal function
serves both paths (app/referral.py); its reasons are tested in
tests/test_referral.py. The `source` field is covered by
tests/test_register_source.py (#863).
"""
import contextlib
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import accounts, agent_profile, referral

KNOWN = "did:moltrust:aaaaaaaaaaaaaaaa"
REVOKED = "did:moltrust:bbbbbbbbbbbbbbbb"
UNKNOWN = "did:moltrust:cccccccccccccccc"


class Conn:
    def __init__(self, db):
        self.db = db

    async def fetchval(self, sql, *args):
        if "FROM api_keys" in sql:
            return None
        return 0                       # every COUNT gate: nothing in the window

    async def fetchrow(self, sql, *args):
        if "FROM agents WHERE did" in sql:
            did = args[0]
            if did not in self.db.agents:
                return None
            return {"revoked": self.db.agents[did]}
        return None

    async def execute(self, sql, *args):
        self.db.statements.append(sql)
        if sql.startswith("INSERT INTO agents "):
            self.db.agents[args[0]] = False
            self.db.inserted.append(args[0])
        if sql.startswith("INSERT INTO agent_referrals"):
            self.db.referrals[args[0]] = args[1]
        return "INSERT 0 1"

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self):
        self.agents = {KNOWN: False, REVOKED: True}
        self.inserted, self.statements = [], []
        self.referrals = {}

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield Conn(self)


@pytest.fixture
def env(monkeypatch):
    pool = Pool()
    refreshed = []

    async def none(*a, **k):
        return None

    async def refresh(_pool, did):
        refreshed.append(did)

    monkeypatch.setattr(m, "db_pool", pool)
    monkeypatch.setattr(m, "check_registration_rate", lambda *a, **k: None)
    monkeypatch.setattr(m, "anchor_to_base", none)
    monkeypatch.setattr(m, "issue_credential",
                        lambda did, typ, claims: {"issuer": "did:web:api.moltrust.ch"})
    monkeypatch.setattr(m, "vc_valid_from", lambda vc: "2026-10-20T00:00:00Z")
    monkeypatch.setattr(m, "vc_valid_until", lambda vc: "2027-10-20T00:00:00Z")
    monkeypatch.setattr(m, "get_primary_proof_value", lambda vc: "z")
    monkeypatch.setattr(m, "link_api_key_to_did", none)
    monkeypatch.setattr(m, "ensure_balance_row", none)
    monkeypatch.setattr(m, "grant_credits", none)
    monkeypatch.setattr(accounts, "payer_ref_for_key", none)
    monkeypatch.setattr(agent_profile, "store_declared", none)
    monkeypatch.setattr(agent_profile, "enrich_in_background", none)
    monkeypatch.setattr(referral, "refresh_quietly", refresh)
    monkeypatch.setattr(m.limiter, "enabled", False)
    m.app.dependency_overrides[m.verify_api_key] = lambda: "mt_test_key"
    try:
        yield pool, refreshed, TestClient(m.app)
    finally:
        m.app.dependency_overrides.pop(m.verify_api_key, None)


def register(client, **body):
    return client.post("/identity/register",
                       json={"display_name": f"km1-{uuid.uuid4().hex[:6]}", **body})


def test_a_registered_referrer_is_recorded(env):
    pool, refreshed, client = env
    r = register(client, referrer_did=KNOWN)
    assert r.status_code == 200, r.text
    did = r.json()["did"]
    assert pool.referrals == {did: KNOWN}
    assert refreshed == [KNOWN]
    assert r.json()["referrer"] == {"did": KNOWN, "recorded": True,
                                    "credential": referral.CREDENTIAL_TYPE}


def test_the_referral_is_checked_before_the_agent_row_and_written_after(env):
    pool, _, client = env
    register(client, referrer_did=KNOWN)
    order = [s.split(" (")[0] for s in pool.statements
             if s.startswith(("INSERT INTO agents ", "INSERT INTO agent_referrals"))]
    assert order == ["INSERT INTO agents", "INSERT INTO agent_referrals"]


@pytest.mark.parametrize("named, reason", [
    (UNKNOWN, referral.REFUSE_UNKNOWN),
    (REVOKED, referral.REFUSE_REVOKED),
])
def test_an_unknown_or_revoked_referrer_is_refused_and_nothing_is_written(env, named, reason):
    pool, refreshed, client = env
    r = register(client, referrer_did=named)
    assert r.status_code == 400
    assert r.json()["detail"] == f"invalid referrer_did: {reason}"
    assert pool.inserted == [] and pool.referrals == {} and refreshed == []


def test_its_own_did_is_refused(env, monkeypatch):
    pool, refreshed, client = env
    fixed = uuid.UUID("dddddddddddddddd" + "0" * 16)
    monkeypatch.setattr(m.uuid, "uuid4", lambda: fixed)
    r = register(client, referrer_did="did:moltrust:dddddddddddddddd")
    assert r.status_code == 400
    assert r.json()["detail"] == f"invalid referrer_did: {referral.REFUSE_OWN}"
    assert pool.inserted == [] and pool.referrals == {} and refreshed == []


def test_a_malformed_referrer_is_a_422_and_nothing_is_written(env):
    pool, _, client = env
    r = register(client, referrer_did="aaaaaaaaaaaaaaaa")
    assert r.status_code == 422
    assert pool.inserted == [] and pool.referrals == {}


def test_no_referrer_writes_no_referral(env):
    """Silence check: the path without a referral is unchanged."""
    pool, refreshed, client = env
    r = register(client)
    assert r.status_code == 200, r.text
    assert len(pool.inserted) == 1
    assert pool.referrals == {} and refreshed == []
    assert "referrer" not in r.json()


def test_both_paths_share_one_shape_check():
    for model in (m.RegisterRequest, m.PopRegisterRequest):
        assert "referrer_did" in model.model_fields
    with pytest.raises(ValueError):
        referral.referrer_did_shape("aaaa")
    assert referral.referrer_did_shape("  ") is None
    assert referral.referrer_did_shape(f" {KNOWN} ") == KNOWN
