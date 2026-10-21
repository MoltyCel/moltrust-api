"""GET /join?ref=<did> keeps a usable referrer and drops every other one (ops KM2).

Until KM2 /join sent any ref on to https://moltrust.ch?ref=, where the page
read it into a meta tag and nothing more: 491 visits in 30 days and not one
could produce a referral. Now a registered, non-revoked did:moltrust goes on to
for-agents.html?referrer=, whose registration commands carry it as
referrer_did. Everything else is dropped, so a stale or invented link leads to
a registration without a referrer rather than to the 400 that both
registration paths give an unknown or revoked one.

No database: the pool answers the one lookup /join makes, the same
`known_referrer` query the registration paths use.
"""
import contextlib

import pytest
from fastapi.testclient import TestClient

import app.main as m

KNOWN = "did:moltrust:aaaaaaaaaaaaaaaa"
REVOKED = "did:moltrust:bbbbbbbbbbbbbbbb"
UNKNOWN = "did:moltrust:cccccccccccccccc"
TARGET = "https://moltrust.ch/for-agents.html"


class Conn:
    def __init__(self, agents, asked):
        self.agents, self.asked = agents, asked

    async def fetchrow(self, sql, *args):
        if "FROM agents WHERE did" not in sql:
            return None                # middleware lookups, not the referrer
        self.asked.append(args[0])
        if args[0] not in self.agents:
            return None
        return {"revoked": self.agents[args[0]]}


class Pool:
    def __init__(self):
        self.agents = {KNOWN: False, REVOKED: True}
        self.asked = []

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield Conn(self.agents, self.asked)


class BrokenPool:
    @contextlib.asynccontextmanager
    async def acquire(self):
        raise RuntimeError("database down")
        yield  # pragma: no cover


@pytest.fixture
def client(monkeypatch):
    pool = Pool()
    monkeypatch.setattr(m, "db_pool", pool)
    return TestClient(m.app), pool


def location(c, query):
    r = c.get("/join" + query, follow_redirects=False)
    assert r.status_code == 302
    return r.headers["location"]


def test_a_registered_did_reaches_the_registration_page(client):
    c, pool = client
    assert location(c, f"?ref={KNOWN}") == f"{TARGET}?referrer=did%3Amoltrust%3Aaaaaaaaaaaaaaaaa"
    assert pool.asked == [KNOWN]


@pytest.mark.parametrize("ref", [UNKNOWN, REVOKED])
def test_an_unknown_or_revoked_did_is_dropped(client, ref):
    c, pool = client
    assert location(c, f"?ref={ref}") == TARGET
    assert pool.asked == [ref]


@pytest.mark.parametrize("ref", ["campaign-x", "did:web:evil.example",
                                 "did:moltrust:short", "did:moltrust:aaaa%0D%0ALocation:x"])
def test_a_ref_without_the_did_shape_is_dropped_without_a_lookup(client, ref):
    c, pool = client
    assert location(c, f"?ref={ref}") == TARGET
    assert pool.asked == []


def test_no_ref_goes_to_the_registration_page_without_a_parameter(client):
    c, pool = client
    assert location(c, "") == TARGET
    assert pool.asked == []


def test_a_database_that_cannot_be_asked_drops_the_ref(monkeypatch):
    monkeypatch.setattr(m, "db_pool", BrokenPool())
    assert location(TestClient(m.app), f"?ref={KNOWN}") == TARGET


def test_the_badge_names_the_referral_link():
    import inspect
    src = inspect.getsource(m)
    assert src.count("Register with this agent as referrer: https://api.moltrust.ch/join?ref={agent_did}") == 2
