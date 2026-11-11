"""POST /identity/register-pop stores `source` like /identity/register does (ops AMB-1).

PopRegisterRequest declared `source` from the start and register_agent_pop
never wrote it, so every keyless registration read as "no channel" in
agent_source — including the ones /join now sends on with ?source=moltbook.
The write is the same statement as on the keyed path: once, truncated to 64,
ON CONFLICT DO NOTHING.

No database: the pool from tests/test_register_referral.py answers the gate
queries, and the three proofs (challenge, PoW, signature) are stubbed — they
are tested in their own suites, this one pins what is written afterwards.
"""
import uuid

import pytest

import app.main as m
from tests.test_register_referral import KNOWN, env  # noqa: F401  (fixture)

PUB = "ab" * 32


@pytest.fixture
def pop(env, monkeypatch):  # noqa: F811
    pool, _, client = env
    calls = []

    ok = lambda *a, **k: (True, None)
    monkeypatch.setattr(m, "verify_challenge", ok)
    monkeypatch.setattr(m, "verify_pow", ok)
    monkeypatch.setattr(m, "verify_pop", ok)
    monkeypatch.setattr(m, "pow_seed", lambda ch: "seed")

    async def archive(*a, **k):
        return None
    monkeypatch.setattr(m._vc_archive, "archive", archive)

    # The stand-in records statements only; keep the arguments as well, the
    # source value is what this suite is about.
    from tests import test_register_referral as base
    real_execute = base.Conn.execute

    async def execute(self, sql, *args):
        calls.append((sql, args))
        return await real_execute(self, sql, *args)
    monkeypatch.setattr(base.Conn, "execute", execute)
    return pool, calls, client


def register_pop(client, **extra):
    body = {"public_key": PUB, "challenge": "c", "signature": "s", "pow_nonce": "n",
            "display_name": f"amb1-{uuid.uuid4().hex[:6]}", **extra}
    return client.post("/identity/register-pop", json=body)


def source_rows(calls):
    return [args for sql, args in calls if sql.startswith("INSERT INTO agent_source")]


def test_a_named_source_is_written_for_the_new_did(pop):
    _, calls, client = pop
    r = register_pop(client, source="moltbook")
    assert r.status_code == 200, r.text
    assert source_rows(calls) == [(r.json()["did"], "moltbook")]


def test_the_source_write_is_the_one_register_uses(pop):
    _, calls, client = pop
    register_pop(client, source="moltbook")
    sql = [s for s, _ in calls if s.startswith("INSERT INTO agent_source")]
    assert sql == [("INSERT INTO agent_source (did, source) VALUES ($1, $2) "
                    "ON CONFLICT (did) DO NOTHING")]


def test_source_and_referrer_are_both_recorded(pop):
    pool, calls, client = pop
    r = register_pop(client, source="moltbook", referrer_did=KNOWN)
    assert r.status_code == 200, r.text
    did = r.json()["did"]
    assert pool.referrals == {did: KNOWN}
    assert source_rows(calls) == [(did, "moltbook")]


def test_without_a_source_nothing_is_written(pop):
    _, calls, client = pop
    r = register_pop(client)
    assert r.status_code == 200, r.text
    assert source_rows(calls) == []


def test_an_overlong_source_is_refused_before_anything_is_written(pop):
    pool, calls, client = pop
    r = register_pop(client, source="x" * 65)
    assert r.status_code == 422
    assert pool.inserted == [] and source_rows(calls) == []
