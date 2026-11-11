"""Every route that grades, scores, badges or verifies a DID, for a revoked one.

#639 made /identity/verify and the JSON badge answer `verified: false` for a
revoked DID. The other routes that print a grade, a score, a badge or a
verification status kept answering from the score alone, because none of them
read `agents.revoked_at`: the two SVG badges drew "<score> / <grade>", the
wallet and ERC-8004 lookups printed a letter grade, /trust/gate answered ALLOW
and /x402/verify `verified: true, payment_ready: true`.

Each route is asked twice, once for a live DID and once for the same kind of
DID revoked by the row update that POST /identity/revoke makes. The live DID
carries a seeded score of 85 (grade A), so the revoked one would show a grade
if the route did not check. One definition of "revoked": app/revocation.py.
Invariant: docs/invariants/c-no-endpoint-affirms-a-revoked-did.yaml.

Needs the API rig in tests/conftest.py (sandbox database).
"""
import datetime
import secrets
import uuid

import pytest

SEED_SCORE = 85.0


@pytest.fixture(autouse=True)
def _no_rate_limit(app_with_lifespan):
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


@pytest.fixture
async def agent(app_with_lifespan):
    """Factory: agent(revoked=..., wallet=..., erc8004=...) -> dict.

    Every agent has a swarm seed of SEED_SCORE, so a route that does not check
    revocation prints grade A for it.
    """
    from app.main import db_pool
    created: list[dict] = []

    async def _make(revoked: bool = False, wallet: bool = False,
                    erc8004: bool = False, spiffe: bool = False) -> dict:
        a = {
            "did": f"did:moltrust:{uuid.uuid4().hex[:16]}",
            "wallet": "0x" + secrets.token_hex(20) if wallet else None,
            "erc8004": 2_000_000_000 + secrets.randbelow(100_000_000) if erc8004 else None,
            "spiffe": f"spiffe://test.local/rev/{uuid.uuid4().hex[:12]}" if spiffe else None,
        }
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agents (did, display_name, platform, agent_type, "
                    "wallet_address, wallet_chain, wallet_bound_at, erc8004_agent_id) "
                    "VALUES ($1, $2, 'test', 'external', $3, $4, $5, $6)",
                    a["did"], f"rv-{a['did'][-8:]}", a["wallet"],
                    "base" if wallet else None,
                    datetime.datetime.utcnow() if wallet else None,
                    a["erc8004"],
                )
                await conn.execute(
                    "INSERT INTO swarm_seeds (did, base_score) VALUES ($1, $2) "
                    "ON CONFLICT (did) DO UPDATE SET base_score = EXCLUDED.base_score",
                    a["did"], SEED_SCORE,
                )
                if spiffe:
                    await conn.execute(
                        "INSERT INTO spiffe_bindings (spiffe_uri, did, bound_by) VALUES ($1, $2, $2)",
                        a["spiffe"], a["did"],
                    )
                if revoked:
                    # Exactly the update POST /identity/revoke makes.
                    await conn.execute(
                        "UPDATE agents SET revoked_at = now(), revocation_reason = $1 WHERE did = $2",
                        "test: key compromised", a["did"],
                    )
        created.append(a)
        return a

    yield _make

    async with db_pool.acquire() as conn:
        for a in created:
            did = a["did"]
            await conn.execute("DELETE FROM spiffe_bindings   WHERE did = $1", did)
            await conn.execute("DELETE FROM gate_events       WHERE queried_did = $1", did)
            await conn.execute("DELETE FROM x402_verify_calls WHERE queried_did = $1", did)
            await conn.execute("DELETE FROM trust_score_cache WHERE did = $1", did)
            await conn.execute("DELETE FROM swarm_seeds       WHERE did = $1", did)
            await conn.execute("DELETE FROM agents            WHERE did = $1", did)


def _assert_revoked_info(rev):
    assert rev is not None, "a revoked DID must say so"
    assert rev["at"]
    assert rev["reason"] == "test: key compromised"


# ── the helper itself ──────────────────────────────────────────────────────

def test_revocation_of_is_the_one_definition():
    from app.revocation import revocation_of
    at = datetime.datetime(2026, 10, 20, 12, 0)
    assert revocation_of(None) is None
    assert revocation_of({"revoked_at": None, "revocation_reason": None}) is None
    assert revocation_of({"revoked_at": at, "revocation_reason": "x"}) == {
        "at": at.isoformat(), "reason": "x"}
    # A reason without a timestamp is not a revocation.
    assert revocation_of({"revoked_at": None, "revocation_reason": "x"}) is None
    # A row that never selected revoked_at cannot be judged, and must not read
    # as "not revoked".
    with pytest.raises(KeyError):
        revocation_of({"did": "did:moltrust:0"})


# ── SVG badges ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/badge/{did}", "/identity/badge/{did}.svg"])
async def test_svg_badge_live_shows_grade(async_client, agent, path):
    a = await agent()
    r = await async_client.get(path.format(did=a["did"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "85 / A" in r.text
    assert "REVOKED" not in r.text


@pytest.mark.parametrize("path", ["/badge/{did}", "/identity/badge/{did}.svg"])
async def test_svg_badge_revoked_shows_revoked_and_no_grade(async_client, agent, path):
    a = await agent(revoked=True)
    r = await async_client.get(path.format(did=a["did"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "REVOKED" in r.text
    assert " / " not in r.text, "no score / grade on a revoked badge"


# ── wallet lookups ─────────────────────────────────────────────────────────

async def test_wallet_live(async_client, agent):
    a = await agent(wallet=True)
    body = (await async_client.get(f"/wallet/{a['wallet']}")).json()
    assert body["did"] == a["did"]
    assert body["verified"] is True
    assert body["revoked"] is None
    assert body["grade"] == "A"


async def test_wallet_revoked(async_client, agent):
    a = await agent(wallet=True, revoked=True)
    r = await async_client.get(f"/wallet/{a['wallet']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["did"] == a["did"]
    assert body["verified"] is False
    _assert_revoked_info(body["revoked"])
    assert body["grade"] == "REVOKED"
    assert body["trust_score"] is None


async def test_erc8004_by_address_live(async_client, agent):
    a = await agent(wallet=True)
    r = await async_client.get(f"/identity/erc8004/{a['wallet']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True
    assert body["revoked"] is None


async def test_erc8004_by_address_revoked(async_client, agent):
    a = await agent(wallet=True, revoked=True)
    r = await async_client.get(f"/identity/erc8004/{a['wallet']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moltrust_did"] == a["did"]
    assert body["verified"] is False
    _assert_revoked_info(body["revoked"])


# ── ERC-8004 by agent id and registration file ────────────────────────────

@pytest.fixture
def _no_chain(monkeypatch):
    """The reverse lookup asks Base first; answer for it, so no RPC is made."""
    import app.main as main

    async def _onchain(agent_id):
        return {"agent_id": agent_id, "owner": "0x" + "0" * 40}

    monkeypatch.setattr(main, "resolve_onchain_agent", _onchain)
    monkeypatch.setattr(main, "get_onchain_reputation", lambda agent_id: None)


async def test_resolve_erc8004_live(async_client, agent, _no_chain):
    a = await agent(erc8004=True)
    r = await async_client.get(f"/resolve/erc8004/{a['erc8004']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moltrust_did"] == a["did"]
    assert body["moltrust_verified"] is True
    assert body["moltrust_trust_score"]["grade"] == "A"


async def test_resolve_erc8004_revoked(async_client, agent, _no_chain):
    a = await agent(erc8004=True, revoked=True)
    r = await async_client.get(f"/resolve/erc8004/{a['erc8004']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moltrust_did"] == a["did"]
    assert body["moltrust_verified"] is False
    _assert_revoked_info(body["moltrust_revoked"])
    assert body["moltrust_trust_score"]["grade"] == "REVOKED"
    assert body["moltrust_trust_score"]["score"] is None


async def test_registration_file_live(async_client, agent):
    a = await agent(erc8004=True)
    r = await async_client.get(f"/agents/{a['did']}/erc8004")
    assert r.status_code == 200, r.text
    assert r.json()["active"] is True


async def test_registration_file_revoked(async_client, agent):
    a = await agent(erc8004=True, revoked=True)
    r = await async_client.get(f"/agents/{a['did']}/erc8004")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] is False
    _assert_revoked_info(body["revoked"])


# ── decisions: trust gate and payment readiness ───────────────────────────

async def test_trust_gate_live_allows(async_client, agent):
    a = await agent()
    body = (await async_client.get(f"/trust/gate/{a['did']}")).json()
    assert body["decision"] == "ALLOW"


async def test_trust_gate_revoked_denies(async_client, agent):
    a = await agent(revoked=True)
    body = (await async_client.get(f"/trust/gate/{a['did']}")).json()
    assert body["decision"] == "DENY"
    assert body["reason"] == "revoked"
    assert body["trust_score"] is None
    _assert_revoked_info(body["revoked"])


async def test_x402_verify_live(async_client, agent):
    a = await agent(wallet=True)
    body = (await async_client.get("/x402/verify", params={"did": a["did"]})).json()
    assert body["verified"] is True
    assert body["payment_ready"] is True


async def test_x402_verify_revoked(async_client, agent):
    a = await agent(wallet=True, revoked=True)
    r = await async_client.get("/x402/verify", params={"did": a["did"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is False
    assert body["payment_ready"] is False
    assert body["reason"] == "revoked"
    _assert_revoked_info(body["revoked"])


# ── routes that already checked, now through the shared helper ────────────

async def test_skill_trust_score_live_and_revoked(async_client, agent):
    live, gone = await agent(), await agent(revoked=True)
    assert (await async_client.get(f"/skill/trust-score/{live['did']}")).json()["grade"] == "A"
    body = (await async_client.get(f"/skill/trust-score/{gone['did']}")).json()
    assert body["grade"] == "REVOKED"
    _assert_revoked_info(body["revoked"])


async def test_a2a_agent_card_live_and_revoked(async_client, agent):
    live, gone = await agent(), await agent(revoked=True)
    t = (await async_client.get(f"/a2a/agent-card/{live['did']}")).json()["trust"]
    assert t["verified"] is True and t["revoked"] is None
    t = (await async_client.get(f"/a2a/agent-card/{gone['did']}")).json()["trust"]
    assert t["verified"] is False
    _assert_revoked_info(t["revoked"])


async def test_spiffe_lookup_live_and_revoked(async_client, agent):
    live, gone = await agent(spiffe=True), await agent(spiffe=True, revoked=True)
    body = (await async_client.get(f"/identity/spiffe/{live['spiffe']}")).json()
    assert body["revoked"] is False
    body = (await async_client.get(f"/identity/spiffe/{gone['spiffe']}")).json()
    assert body["revoked"] is True
    assert body["grade"] == "REVOKED"
    _assert_revoked_info(body["revocation"])


# ── swarm graph and recompute ─────────────────────────────────────────────

async def test_swarm_graph_live(async_client, agent):
    a = await agent()
    body = (await async_client.get(f"/swarm/graph/{a['did']}")).json()
    assert body["revoked"] is None
    target = next(n for n in body["nodes"] if n["did"] == a["did"])
    assert target["grade"] == "A"


async def test_swarm_graph_revoked(async_client, agent):
    a = await agent(revoked=True)
    body = (await async_client.get(f"/swarm/graph/{a['did']}")).json()
    _assert_revoked_info(body["revoked"])
    target = next(n for n in body["nodes"] if n["did"] == a["did"])
    assert target["grade"] == "REVOKED"
    assert target["score"] is None


async def test_swarm_propagate_live(async_client, agent):
    a = await agent()
    body = (await async_client.get(f"/swarm/propagate/{a['did']}")).json()
    assert body["grade"] == "A"


async def test_swarm_propagate_revoked(async_client, agent):
    a = await agent(revoked=True)
    body = (await async_client.get(f"/swarm/propagate/{a['did']}")).json()
    assert body["grade"] == "REVOKED"
    assert body["trust_score"] is None
    _assert_revoked_info(body["revoked"])
