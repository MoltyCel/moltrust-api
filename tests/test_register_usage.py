"""POST /identity/register-usage: one call, a DID and a free key (ops WM-3).

No database: the pool below keeps agents, keys and fingerprint bindings in
dicts and answers exactly the statements the route and app/usage_register.py
send. What is pinned:

- an empty body (and no body at all) registers and returns did + api_key;
- the same client address + User-Agent gets the same DID back with 409, no
  second agent row, no new key, and the key from the first call untouched and
  still valid (Lars, 21.11.2026: a repeat call never invalidates or creates a
  key, because another agent may sit behind the same address);
- the module offers no way to retire a key;
- a different User-Agent is a different caller;
- the per-IP rate limit answers 429;
- platform is limited to usage-api and test;
- a missing X-API-Key on a keyed route answers 401 with the one-call offer.

Key values are never printed; assertions compare them, nothing else.
"""
import contextlib
import pathlib
import re
import uuid

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import referral, register_hint, usage_register

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = "/identity/register-usage"


class Conn:
    def __init__(self, db):
        self.db = db

    async def execute(self, sql, *args):
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return "SELECT 1"
        if sql.startswith("INSERT INTO agents "):
            self.db.agents[args[0]] = {"platform": args[2], "revoked": False}
        elif sql.lstrip().startswith("INSERT INTO usage_registrations"):
            self.db.bindings[args[0]] = args[1]
        elif sql.startswith("UPDATE usage_registrations"):
            self.db.touches += 1
        elif sql.startswith("INSERT INTO api_keys"):
            self.db.keys[args[0]] = {"did": args[1], "method": args[2], "active": True}
        elif sql.startswith("INSERT INTO agent_source"):
            self.db.sources[args[0]] = args[1]
        elif sql.startswith("INSERT INTO agent_referrals"):
            self.db.referrals[args[0]] = args[1]
        return "OK"

    async def fetchval(self, sql, *args):
        if "FROM usage_registrations u" in sql:
            did = self.db.bindings.get(args[0])
            if did and not self.db.agents[did]["revoked"] and not self.db.expired:
                return did
            return None
        return None

    async def fetchrow(self, sql, *args):
        if "FROM agents WHERE did" in sql:
            a = self.db.agents.get(args[0])
            return None if a is None else {"revoked": a["revoked"]}
        return None

    async def fetch(self, sql, *args):
        return []

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self):
        self.agents, self.bindings, self.keys = {}, {}, {}
        self.sources, self.referrals = {}, {}
        self.touches = 0
        self.expired = False

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield Conn(self)


KNOWN = "did:moltrust:aaaaaaaaaaaaaaaa"


@pytest.fixture
def env(monkeypatch):
    pool = Pool()
    pool.agents[KNOWN] = {"platform": "a2a", "revoked": False}

    async def none(*a, **k):
        return None

    monkeypatch.setattr(m, "db_pool", pool)
    monkeypatch.setattr(m, "ensure_balance_row", none)
    monkeypatch.setattr(referral, "refresh_quietly", none)
    monkeypatch.setattr(m.limiter, "enabled", False)
    yield pool, TestClient(m.app)


def ua(name):
    return {"User-Agent": name}


def active_keys(pool, did):
    return [k for k, v in pool.keys.items() if v["did"] == did and v["active"]]


def test_an_empty_body_registers_in_one_call(env):
    pool, client = env
    r = client.post(PATH, json={}, headers=ua("agent-a/1.0"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "created"
    assert body["did"].startswith("did:moltrust:")
    assert re.fullmatch(r"mt_[0-9a-f]{32}", body["api_key"])
    assert pool.agents[body["did"]]["platform"] == usage_register.PLATFORM
    assert pool.keys[body["api_key"]] == {"did": body["did"],
                                          "method": usage_register.SIGNUP_METHOD,
                                          "active": True}
    assert body["credits"]["balance"] == 0
    assert body["monthly_credit_floor"] == 0
    assert body["api_key"] in m.API_KEYS


def test_no_body_at_all_registers_too(env):
    pool, client = env
    r = client.post(PATH, headers=ua("agent-nobody/1.0"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created"
    assert len(pool.agents) == 2  # KNOWN plus the new one


def test_the_same_caller_gets_the_same_did_and_no_second_agent(env):
    pool, client = env
    first = client.post(PATH, json={}, headers=ua("agent-b/1.0")).json()
    agents_before = len(pool.agents)

    r = client.post(PATH, json={}, headers=ua("agent-b/1.0"))
    assert r.status_code == 409, r.text
    second = r.json()

    assert second["status"] == "existing" and second["existing"] is True
    assert second["did"] == first["did"]
    assert len(pool.agents) == agents_before
    assert pool.touches == 1


def test_a_repeat_call_never_creates_or_invalidates_a_key(env):
    """Lars, 21.11.2026: a repeat call must never invalidate the key of another
    agent behind the same address. The old key stays valid, no new key exists,
    the answer names the DID and carries no key at all."""
    pool, client = env
    first = client.post(PATH, json={}, headers=ua("agent-nat/1.0")).json()
    keys_before = {k: dict(v) for k, v in pool.keys.items()}

    for _ in range(3):
        r = client.post(PATH, json={}, headers=ua("agent-nat/1.0"))
        assert r.status_code == 409, r.text
        body = r.json()
        assert "api_key" not in body
        assert body["key"] == "not_issued"
        assert body["did"] == first["did"]

    assert pool.keys == keys_before
    assert active_keys(pool, first["did"]) == [first["api_key"]]
    assert first["api_key"] in m.API_KEYS


def test_a_repeat_call_points_at_the_existing_key_and_the_pop_route(env):
    _, client = env
    client.post(PATH, json={}, headers=ua("agent-hint/1.0"))
    body = client.post(PATH, json={}, headers=ua("agent-hint/1.0")).json()

    assert "first call" in body["use_existing_key"]
    assert "/identity/register-pop" in body["register_with_own_key"]
    assert body["how_to_register"] == register_hint.OWN_KEY_TEXT
    hint = body["register_hint"]
    assert hint["via"] == "register-usage-repeat"
    paths = [s["path"] for s in hint["steps"]]
    # The pop route from register_hint, and not the one-call step, which would
    # only lead back to this same answer.
    assert paths[0] == "/identity/register-pop"
    assert PATH not in paths
    assert hint["steps"][0]["example_body"] == register_hint.EXAMPLE_BODY
    # The shared hint object is not edited by building this one.
    assert register_hint.HINT_OBJECT["steps"][0]["path"] == PATH


def test_the_module_has_no_key_retirement():
    """No rotation through this route: the helper that retired keys is gone,
    and the module sends no statement that deactivates a key."""
    assert not hasattr(usage_register, "retire_keys")
    src = (ROOT / "app" / "usage_register.py").read_text()
    assert "SET active" not in src
    route = (ROOT / "app" / "main.py").read_text()
    start = route.index("async def register_on_use")
    end = route.index("\n@app.", start)
    assert "retire_keys" not in route[start:end]
    assert "UPDATE api_keys" not in route[start:end]
    assert "API_KEYS.discard" not in route[start:end]


def test_a_different_user_agent_is_a_different_caller(env):
    pool, client = env
    a = client.post(PATH, json={}, headers=ua("agent-c/1.0")).json()
    b = client.post(PATH, json={}, headers=ua("agent-c/2.0")).json()
    assert a["did"] != b["did"]


def test_after_the_window_the_same_caller_registers_anew(env):
    pool, client = env
    a = client.post(PATH, json={}, headers=ua("agent-d/1.0")).json()
    pool.expired = True
    b = client.post(PATH, json={}, headers=ua("agent-d/1.0")).json()
    assert b["status"] == "created" and b["did"] != a["did"]
    # The first key is not touched by the second registration either.
    assert pool.keys[a["api_key"]]["active"] is True


def test_optional_fields_are_stored(env):
    pool, client = env
    r = client.post(PATH, json={"display_name": "my agent", "source": "402-credits",
                                "referrer_did": KNOWN}, headers=ua("agent-e/1.0"))
    assert r.status_code == 200, r.text
    did = r.json()["did"]
    assert pool.sources[did] == "402-credits"
    assert pool.referrals[did] == KNOWN


def test_an_unknown_referrer_is_refused_and_nothing_is_written(env):
    pool, client = env
    before = dict(pool.agents)
    r = client.post(PATH, json={"referrer_did": "did:moltrust:cccccccccccccccc"},
                    headers=ua("agent-f/1.0"))
    assert r.status_code == 400
    assert pool.agents == before and pool.keys == {}


@pytest.mark.parametrize("platform, ok", [("test", True), ("usage-api", True),
                                          ("a2a", False), ("moltbook", False)])
def test_platform_is_usage_api_or_test(env, platform, ok):
    _, client = env
    r = client.post(PATH, json={"platform": platform},
                    headers=ua(f"agent-p-{uuid.uuid4().hex[:6]}"))
    assert (r.status_code == 200) is ok, r.text


def test_the_route_never_anchors(env, monkeypatch):
    called = []

    async def anchor(*a, **k):
        called.append(a)
        return None, "x"
    monkeypatch.setattr(m, "anchor_registration", anchor)
    monkeypatch.setattr(m, "anchor_to_base", anchor)
    _, client = env
    assert client.post(PATH, json={}, headers=ua("agent-g/1.0")).status_code == 200
    assert called == []


def test_the_per_ip_rate_limit_answers_429(env, monkeypatch):
    _, client = env
    monkeypatch.setattr(m.limiter, "enabled", True)
    m.limiter.reset()
    allowed = int(usage_register.RATE_LIMIT.split("/")[0])
    codes = [client.post(PATH, json={}, headers=ua(f"rot-{i}")).status_code
             for i in range(allowed + 1)]
    m.limiter.reset()
    assert codes[:allowed] == [200] * allowed
    assert codes[allowed] == 429


def test_a_call_without_a_key_gets_the_one_call_offer():
    """401 on a keyed route: the detail string is unchanged in kind, the hint
    sits next to it with the one-call step first."""
    client = TestClient(m.app)
    r = client.post("/identity/register", json={"display_name": "x"})
    assert r.status_code == 401, r.text
    body = r.json()
    assert isinstance(body["detail"], str) and "X-API-Key" in body["detail"]
    steps = body["register_hint"]["steps"]
    assert steps[0]["path"] == PATH and steps[0]["example_body"] == {}
    assert any(s["path"] == "/identity/register-pop" for s in steps)
    assert body["register_hint"]["via"] == "401-key-required"
    assert PATH in body["how_to_register"]


def test_the_402_hint_leads_with_the_one_call_step():
    hint = register_hint.with_hint({"error": "insufficient_credits"}, source="402-credits")
    assert hint["register_hint"]["steps"][0]["path"] == PATH
    assert PATH in hint["how_to_register"]


def test_fingerprint_is_a_hash_of_address_and_agent():
    fp = usage_register.fingerprint("203.0.113.7", "agent/1.0")
    assert re.fullmatch(r"[0-9a-f]{64}", fp)
    assert "203.0.113.7" not in fp
    assert fp == usage_register.fingerprint("203.0.113.7", "agent/1.0")
    assert fp != usage_register.fingerprint("203.0.113.7", "agent/2.0")
    assert fp != usage_register.fingerprint("203.0.113.8", "agent/1.0")


def test_the_invariant_pins_one_key_per_usage_did():
    import yaml
    inv = yaml.safe_load((ROOT / "docs" / "invariants" /
                          "c-usage-register-one-key-per-did.yaml").read_text())
    assert inv["id"] == "c-usage-register-one-key-per-did"
    sql = inv["abfrage"]["wert"]
    assert f"'{usage_register.SIGNUP_METHOD}'" in sql
    assert "count(*) > 1" in sql and "NOT active" in sql
    assert inv["erwartung"] == {"operator": "eq", "wert": 0}


def test_the_usage_platform_list_is_the_same_in_all_three_places():
    counts = (ROOT / "app" / "sql" / "agent_counts.sql").read_text()
    in_counts = counts.split("AS revoked,", 1)[1]
    assert set(re.findall(r"'([a-z0-9-]+)'", in_counts)) == set(usage_register.USAGE_PLATFORMS)

    export = (ROOT / "app" / "sql" / "registry_export.sql").read_text()
    body = export.split("bucket_map(platform, bucket) AS (VALUES", 1)[1].split("\n),", 1)[0]
    usage = {p for p, b in re.findall(r"\('([^']*)','([^']*)'\)", body) if b == "usage"}
    assert usage == set(usage_register.USAGE_PLATFORMS)


def test_usage_is_the_last_column_of_agent_counts():
    """Appended, not inserted: daily_stats.sh reads the row by position."""
    counts = (ROOT / "app" / "sql" / "agent_counts.sql").read_text()
    aliases = re.findall(r"\)\s+AS\s+([a-z_]+)", counts)
    assert aliases[-7:] == ["registered", "active", "active_window_days", "test",
                            "partner_test", "revoked", "usage"]
    sh = (ROOT / "scripts" / "daily_stats.sh").read_text()
    assert ("read -r REGISTERED ACTIVE ACTIVE_WINDOW TEST_AGENTS PARTNER_TEST "
            "REVOKED USAGE_AGENTS") in sh
