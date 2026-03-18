"""
MolTrust Swarm Intelligence — Sandbox Test Suite
Läuft gegen http://localhost:8005 (Sandbox, Port 8005)
Nie gegen Port 8000 (Produktion)
"""
import pytest, httpx, hashlib, json, os, asyncio, asyncpg, sys, math
from datetime import datetime, timezone, timedelta

BASE = "http://localhost:8005"
DB_PW = os.getenv("MOLTSTACK_DB_PW", "")

# Ensure app module is importable
sys.path.insert(0, "/home/moltstack/moltstack")

async def _sandbox_conn():
    return await asyncpg.connect(
        host="localhost", database="moltstack_sandbox",
        user="moltstack", password=DB_PW
    )

async def _insert_test_endorsement(
    conn, endorser, endorsed, skill="python",
    vertical="skill", days_ago=0, hash_suffix="001"
):
    issued = datetime.now(timezone.utc) - timedelta(days=days_ago)
    expires = issued + timedelta(days=90)
    await conn.execute("""
        INSERT INTO endorsements
          (endorser_did, endorsed_did, skill, evidence_hash,
           evidence_timestamp, vertical, issued_at, expires_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT DO NOTHING
    """, endorser, endorsed, skill,
        f"hash_{hash_suffix}", issued, vertical, issued, expires)

# ─── HARNESS: Bestehende Endpoints müssen weiterhin funktionieren ───

def test_health():
    r = httpx.get(f"{BASE}/health")
    assert r.status_code == 200
    assert r.json()["database"] == "connected"

def test_skills_list():
    r = httpx.get(f"{BASE}/skills")
    assert r.status_code == 200

def test_credits_pricing():
    r = httpx.get(f"{BASE}/credits/pricing")
    assert r.status_code == 200

def test_well_known_did():
    r = httpx.get(f"{BASE}/.well-known/did.json")
    assert r.status_code == 200

def test_stats():
    r = httpx.get(f"{BASE}/stats")
    assert r.status_code == 200

def test_agents_recent():
    r = httpx.get(f"{BASE}/agents/recent")
    assert r.status_code == 200

def test_identity_verify_not_found():
    r = httpx.get(f"{BASE}/identity/verify/did:moltrust:nonexistent")
    assert r.status_code in [200, 400, 404]

# ─── STEP 1: DB Schema ───

def test_endorsements_table_exists():
    """Sandbox-DB muss endorsements Tabelle haben"""
    async def check():
        conn = await _sandbox_conn()
        result = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_name = 'endorsements')"
        )
        await conn.close()
        return result
    assert asyncio.run(check()) is True

def test_trust_score_cache_table_exists():
    """Sandbox-DB muss trust_score_cache Tabelle haben"""
    async def check():
        conn = await _sandbox_conn()
        result = await conn.fetchval(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_name = 'trust_score_cache')"
        )
        await conn.close()
        return result
    assert asyncio.run(check()) is True

def test_endorsements_unique_constraint():
    """UNIQUE(endorser_did, endorsed_did, evidence_hash) muss enforced sein"""
    async def check():
        conn = await _sandbox_conn()
        await conn.execute("""
            INSERT INTO endorsements
            (endorser_did, endorsed_did, skill, evidence_hash,
             evidence_timestamp, vertical, expires_at)
            VALUES ('did:test:a', 'did:test:b', 'test',
                    'hash_unique_001', NOW(), 'skill',
                    NOW() + INTERVAL '90 days')
            ON CONFLICT DO NOTHING
        """)
        result = await conn.execute("""
            INSERT INTO endorsements
            (endorser_did, endorsed_did, skill, evidence_hash,
             evidence_timestamp, vertical, expires_at)
            VALUES ('did:test:a', 'did:test:b', 'test',
                    'hash_unique_001', NOW(), 'skill',
                    NOW() + INTERVAL '90 days')
            ON CONFLICT DO NOTHING
        """)
        await conn.execute(
            "DELETE FROM endorsements WHERE evidence_hash = 'hash_unique_001'"
        )
        await conn.close()
        return result
    result = asyncio.run(check())
    assert "INSERT 0 0" in result

# ─── STEP 2: Trust Score Algorithmus ───

def test_time_decay_fresh():
    """Endorsement von heute: d_i ~ 1.0"""
    from app.swarm.trust_score import compute_time_decay
    now = datetime.now(timezone.utc)
    d = compute_time_decay(now)
    assert 0.99 <= d <= 1.0

def test_time_decay_45_days():
    """45 Tage alt: d_i = 2^(-0.5) ~ 0.707"""
    from app.swarm.trust_score import compute_time_decay
    issued = datetime.now(timezone.utc) - timedelta(days=45)
    d = compute_time_decay(issued)
    assert abs(d - 0.707) < 0.01

def test_time_decay_90_days():
    """90 Tage alt: d_i = 0.5 (half-life)"""
    from app.swarm.trust_score import compute_time_decay
    issued = datetime.now(timezone.utc) - timedelta(days=90)
    d = compute_time_decay(issued)
    assert abs(d - 0.5) < 0.01

def test_score_withheld_below_3_endorsers():
    """< 3 Endorser -> score muss None (withheld) sein"""
    from app.swarm.trust_score import compute_trust_score
    async def run():
        conn = await _sandbox_conn()
        target = "did:test:target_w"
        await _insert_test_endorsement(
            conn, "did:test:e1", target, hash_suffix="w1")
        await _insert_test_endorsement(
            conn, "did:test:e2", target, hash_suffix="w2")
        score = await compute_trust_score(target, conn)
        await conn.execute(
            "DELETE FROM endorsements WHERE endorsed_did = $1", target)
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", target)
        await conn.close()
        return score
    result = asyncio.run(run())
    assert result is None

def test_score_visible_with_3_endorsers():
    """>= 3 Endorser -> score muss > 0 sein"""
    from app.swarm.trust_score import compute_trust_score
    async def run():
        conn = await _sandbox_conn()
        target = "did:test:target_v"
        for i in range(3):
            await _insert_test_endorsement(
                conn, f"did:test:end{i}", target, hash_suffix=f"v{i}")
        score = await compute_trust_score(target, conn)
        await conn.execute(
            "DELETE FROM endorsements WHERE endorsed_did = $1", target)
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", target)
        await conn.close()
        return score
    result = asyncio.run(run())
    assert result is not None
    assert result > 0

def test_new_agent_default_weight():
    """Neuer Agent ohne Score: w_i = 0.1 (bootstrapping)"""
    from app.swarm.trust_score import get_endorser_weight
    async def run():
        conn = await _sandbox_conn()
        w = await get_endorser_weight(
            "did:test:new_agent_no_score", conn)
        await conn.close()
        return w
    result = asyncio.run(run())
    assert result == 0.1

def test_cache_written_after_computation():
    """Nach compute_trust_score soll Cache-Eintrag existieren"""
    from app.swarm.trust_score import compute_trust_score
    async def run():
        conn = await _sandbox_conn()
        target = "did:test:cache_target"
        for i in range(3):
            await _insert_test_endorsement(
                conn, f"did:test:ce{i}", target, hash_suffix=f"c{i}")
        await compute_trust_score(target, conn)
        cached = await conn.fetchrow(
            "SELECT score FROM trust_score_cache WHERE did = $1", target)
        await conn.execute(
            "DELETE FROM endorsements WHERE endorsed_did = $1", target)
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", target)
        await conn.close()
        return cached
    result = asyncio.run(run())
    assert result is not None

# ─── STEP 3: Anti-Collusion ───

def test_jaccard_penalty_mutual_ring():
    """3 Agents die sich gegenseitig endorsieren -> Jaccard penalty > 0"""
    from app.swarm.anti_collusion import compute_sybil_penalty
    async def run():
        conn = await _sandbox_conn()
        ring = ["did:test:ring1", "did:test:ring2", "did:test:ring3"]
        target = "did:test:ring_target"
        # Ring: jeder endorsed jeden
        for i, a in enumerate(ring):
            for j, b in enumerate(ring):
                if a != b:
                    await _insert_test_endorsement(
                        conn, a, b, hash_suffix=f"ring_{i}_{j}",
                        vertical=["skill", "shopping", "travel"][i])
            # Jeder endorsed auch das Target
            await _insert_test_endorsement(
                conn, a, target, hash_suffix=f"ring_t_{i}",
                vertical=["skill", "shopping", "travel"][i])
        penalty = await compute_sybil_penalty(
            target, set(ring), conn)
        # Cleanup
        for did in ring + [target]:
            await conn.execute(
                "DELETE FROM endorsements WHERE endorsed_did = $1", did)
            await conn.execute(
                "DELETE FROM endorsements WHERE endorser_did = $1", did)
        await conn.close()
        return penalty
    result = asyncio.run(run())
    # Mutual ring -> Jaccard > 0.8 -> penalty > 0
    assert result > 0

def test_vertical_diversity_penalty():
    """Alle Endorsements aus nur 1 Vertical -> penalty = 10.0"""
    from app.swarm.anti_collusion import compute_sybil_penalty
    async def run():
        conn = await _sandbox_conn()
        target = "did:test:mono_target"
        endorsers = set()
        for i in range(3):
            e = f"did:test:mono_e{i}"
            endorsers.add(e)
            await _insert_test_endorsement(
                conn, e, target, vertical="skill",
                hash_suffix=f"mono_{i}")
        penalty = await compute_sybil_penalty(
            target, endorsers, conn)
        await conn.execute(
            "DELETE FROM endorsements WHERE endorsed_did = $1", target)
        await conn.close()
        return penalty
    result = asyncio.run(run())
    assert result >= 10.0  # vertical diversity penalty

def test_no_penalty_diverse_endorsers():
    """3 unabhängige Endorser aus 3 Verticals, kein Ring -> penalty = 0"""
    from app.swarm.anti_collusion import compute_sybil_penalty
    async def run():
        conn = await _sandbox_conn()
        target = "did:test:diverse_target"
        endorsers = set()
        verticals = ["skill", "shopping", "travel"]
        for i in range(3):
            e = f"did:test:diverse_e{i}"
            endorsers.add(e)
            await _insert_test_endorsement(
                conn, e, target, vertical=verticals[i],
                hash_suffix=f"diverse_{i}")
        penalty = await compute_sybil_penalty(
            target, endorsers, conn)
        await conn.execute(
            "DELETE FROM endorsements WHERE endorsed_did = $1", target)
        await conn.close()
        return penalty
    result = asyncio.run(run())
    assert result == 0.0

# ─── STEP 4: Interaction Proof Endpoint ───

TEST_API_KEY = "mt_test_key_2026"
TEST_DID = "did:moltrust:455d06aa3d9d4fac"

def test_interaction_proof_creates_hash():
    """POST /skill/interaction-proof returns evidence_hash"""
    r = httpx.post(f"{BASE}/skill/interaction-proof", json={
        "api_key": TEST_API_KEY,
        "interaction_payload": {
            "type": "skill_verification",
            "agent_a": TEST_DID,
            "agent_b": "did:test:counterparty",
            "timestamp": "2026-03-17T20:00:00Z",
            "outcome": "verified"
        }
    })
    assert r.status_code == 200
    data = r.json()
    assert "evidence_hash" in data
    assert data["evidence_hash"].startswith("sha256:")
    assert len(data["evidence_hash"]) == 71  # sha256: + 64 hex chars

def test_interaction_proof_anchors_on_chain():
    """POST /skill/interaction-proof returns base_tx_hash"""
    r = httpx.post(f"{BASE}/skill/interaction-proof", json={
        "api_key": TEST_API_KEY,
        "interaction_payload": {
            "type": "trade",
            "agent_a": TEST_DID,
            "agent_b": "did:test:other_agent",
            "timestamp": "2026-03-17T21:00:00Z",
            "outcome": "completed"
        }
    })
    assert r.status_code == 200
    data = r.json()
    assert "base_tx_hash" in data
    assert data["base_tx_hash"].startswith("0x")

def test_interaction_proof_invalid_api_key():
    """Invalid API key returns 400"""
    r = httpx.post(f"{BASE}/skill/interaction-proof", json={
        "api_key": "invalid_key_xxx",
        "interaction_payload": {
            "type": "test",
            "agent_a": "did:test:a",
            "agent_b": "did:test:b",
            "timestamp": "now",
            "outcome": "ok"
        }
    })
    assert r.status_code == 400

def test_interaction_proof_self_interaction_rejected():
    """agent_a == agent_b returns 400"""
    r = httpx.post(f"{BASE}/skill/interaction-proof", json={
        "api_key": TEST_API_KEY,
        "interaction_payload": {
            "type": "test",
            "agent_a": TEST_DID,
            "agent_b": TEST_DID,
            "timestamp": "now",
            "outcome": "ok"
        }
    })
    assert r.status_code == 400
    assert "different" in r.json()["detail"]

def test_interaction_proof_deterministic_hash():
    """Same payload produces same hash"""
    payload = {
        "type": "deterministic_test",
        "agent_a": TEST_DID,
        "agent_b": "did:test:det_b",
        "timestamp": "2026-03-17T12:00:00Z",
        "outcome": "success"
    }
    r1 = httpx.post(f"{BASE}/skill/interaction-proof", json={
        "api_key": TEST_API_KEY, "interaction_payload": payload
    })
    r2 = httpx.post(f"{BASE}/skill/interaction-proof", json={
        "api_key": TEST_API_KEY, "interaction_payload": payload
    })
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["evidence_hash"] == r2.json()["evidence_hash"]



# ─── Helper: get two different API keys ───

async def _get_two_api_keys(conn):
    rows = await conn.fetch(
        "SELECT key, owner_did FROM api_keys "
        "WHERE active = true AND owner_did IS NOT NULL AND owner_did != '' "
        "LIMIT 2"
    )
    if len(rows) < 2:
        raise RuntimeError("Need >= 2 active API keys")
    return rows[0]["key"], rows[0]["owner_did"], rows[1]["key"], rows[1]["owner_did"]

# ─── STEP 5: POST /skill/endorse ───

def test_endorse_creates_vc():
    """POST /skill/endorse returns W3C SkillEndorsementCredential"""
    async def run():
        conn = await _sandbox_conn()
        key1, did1, key2, did2 = await _get_two_api_keys(conn)
        await conn.close()
        proof_r = httpx.post(f"{BASE}/skill/interaction-proof", json={
            "api_key": key1,
            "interaction_payload": {
                "type": "skill_verification",
                "agent_a": did1, "agent_b": did2,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "outcome": "verified"
            }
        })
        assert proof_r.status_code == 200
        evidence = proof_r.json()
        r = httpx.post(f"{BASE}/skill/endorse", json={
            "api_key": key1, "endorsed_did": did2,
            "skill": "python",
            "evidence_hash": evidence["evidence_hash"],
            "evidence_timestamp": evidence["anchored_at"],
            "vertical": "skill"
        })
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        vc = r.json()
        assert "VerifiableCredential" in vc["type"]
        assert "SkillEndorsementCredential" in vc["type"]
        assert vc["credentialSubject"]["id"] == did2
        assert vc["credentialSubject"]["skill"] == "python"
        assert "proof" in vc
        conn = await _sandbox_conn()
        h = evidence["evidence_hash"].removeprefix("sha256:")
        await conn.execute("DELETE FROM endorsements WHERE evidence_hash = $1", h)
        await conn.execute("DELETE FROM trust_score_cache WHERE did = $1", did2)
        await conn.close()
    asyncio.run(run())

def test_endorse_self_endorsement_rejected():
    """Self-endorsement returns 400"""
    async def run():
        conn = await _sandbox_conn()
        key1, did1, key2, did2 = await _get_two_api_keys(conn)
        await conn.close()
        r = httpx.post(f"{BASE}/skill/endorse", json={
            "api_key": key1, "endorsed_did": did1,
            "skill": "python",
            "evidence_hash": "sha256:" + "a" * 64,
            "evidence_timestamp": datetime.now(timezone.utc).isoformat(),
            "vertical": "skill"
        })
        assert r.status_code == 400
        assert "Self-endorsement" in r.json()["detail"]
    asyncio.run(run())

def test_endorse_duplicate_evidence_hash_rejected():
    """Same evidence_hash cannot be used twice"""
    async def run():
        conn = await _sandbox_conn()
        key1, did1, key2, did2 = await _get_two_api_keys(conn)
        await conn.close()
        proof_r = httpx.post(f"{BASE}/skill/interaction-proof", json={
            "api_key": key1,
            "interaction_payload": {
                "type": "dup_test", "agent_a": did1, "agent_b": did2,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "outcome": "ok"
            }
        })
        evidence = proof_r.json()
        r1 = httpx.post(f"{BASE}/skill/endorse", json={
            "api_key": key1, "endorsed_did": did2, "skill": "python",
            "evidence_hash": evidence["evidence_hash"],
            "evidence_timestamp": evidence["anchored_at"],
            "vertical": "skill"
        })
        assert r1.status_code == 200
        r2 = httpx.post(f"{BASE}/skill/endorse", json={
            "api_key": key1, "endorsed_did": did2, "skill": "javascript",
            "evidence_hash": evidence["evidence_hash"],
            "evidence_timestamp": evidence["anchored_at"],
            "vertical": "skill"
        })
        assert r2.status_code == 400
        assert "Duplicate" in r2.json()["detail"]
        conn = await _sandbox_conn()
        h = evidence["evidence_hash"].removeprefix("sha256:")
        await conn.execute("DELETE FROM endorsements WHERE evidence_hash = $1", h)
        await conn.execute("DELETE FROM trust_score_cache WHERE did = $1", did2)
        await conn.close()
    asyncio.run(run())

def test_endorse_invalid_api_key():
    """Invalid API key returns 400"""
    r = httpx.post(f"{BASE}/skill/endorse", json={
        "api_key": "invalid_key_xxx", "endorsed_did": "did:test:someone",
        "skill": "python", "evidence_hash": "sha256:" + "b" * 64,
        "evidence_timestamp": datetime.now(timezone.utc).isoformat(),
        "vertical": "skill"
    })
    assert r.status_code == 400
    assert "Invalid API key" in r.json()["detail"]

def test_endorse_unknown_endorsed_did():
    """Unknown endorsed_did returns 400"""
    async def run():
        conn = await _sandbox_conn()
        rows = await conn.fetch(
            "SELECT key FROM api_keys "
            "WHERE active = true AND owner_did IS NOT NULL AND owner_did != '' "
            "LIMIT 1"
        )
        await conn.close()
        r = httpx.post(f"{BASE}/skill/endorse", json={
            "api_key": rows[0]["key"],
            "endorsed_did": "did:moltrust:nonexistent_xyz",
            "skill": "python", "evidence_hash": "sha256:" + "c" * 64,
            "evidence_timestamp": datetime.now(timezone.utc).isoformat(),
            "vertical": "skill"
        })
        assert r.status_code == 400
        assert "not found" in r.json()["detail"]
    asyncio.run(run())


# ─── STEP 6: GET trust-score + endorsements ───

def test_trust_score_withheld_via_api():
    """GET /skill/trust-score/:did -> withheld when < 3 endorsers"""
    r = httpx.get(f"{BASE}/skill/trust-score/did:test:nobody_xyz")
    assert r.status_code == 200
    data = r.json()
    assert data["withheld"] is True
    assert data["trust_score"] is None

def test_trust_score_visible_via_api():
    """GET /skill/trust-score/:did -> score > 0 after 3 endorsements"""
    async def run():
        conn = await _sandbox_conn()
        key1, did1, key2, did2 = await _get_two_api_keys(conn)
        verticals = ["skill", "shopping", "travel"]
        for i, v in enumerate(verticals):
            await _insert_test_endorsement(
                conn, f"did:test:api_end_{i}", did2,
                vertical=v, hash_suffix=f"api6_{i}")
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", did2)
        await conn.close()
        r = httpx.get(f"{BASE}/skill/trust-score/{did2}")
        assert r.status_code == 200
        data = r.json()
        assert data["withheld"] is False
        assert data["trust_score"] is not None
        assert data["trust_score"] > 0
        assert data["endorser_count"] >= 3
        # Cleanup
        conn = await _sandbox_conn()
        await conn.execute(
            "DELETE FROM endorsements WHERE endorsed_did = $1", did2)
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", did2)
        await conn.close()
    asyncio.run(run())

def test_endorsements_list_via_api():
    """GET /skill/endorsements/:did -> list of endorsements"""
    async def run():
        conn = await _sandbox_conn()
        key1, did1, key2, did2 = await _get_two_api_keys(conn)
        await _insert_test_endorsement(
            conn, did1, did2, hash_suffix="list6_1")
        await conn.close()
        r = httpx.get(f"{BASE}/skill/endorsements/{did2}")
        assert r.status_code == 200
        data = r.json()
        assert "endorsements" in data
        assert "total" in data
        assert isinstance(data["endorsements"], list)
        assert data["total"] >= 1
        assert data["endorsements"][0]["endorser_did"] == did1
        # Cleanup
        conn = await _sandbox_conn()
        await conn.execute(
            "DELETE FROM endorsements WHERE evidence_hash = 'hash_list6_1'")
        await conn.close()
    asyncio.run(run())

def test_endorsements_given_via_api():
    """GET /skill/endorsements/given/:did -> endorsements given"""
    async def run():
        conn = await _sandbox_conn()
        key1, did1, key2, did2 = await _get_two_api_keys(conn)
        await _insert_test_endorsement(
            conn, did1, did2, hash_suffix="given6_1")
        await conn.close()
        r = httpx.get(f"{BASE}/skill/endorsements/given/{did1}")
        assert r.status_code == 200
        data = r.json()
        assert "endorsements_given" in data
        assert "total" in data
        assert isinstance(data["endorsements_given"], list)
        assert data["total"] >= 1
        # Cleanup
        conn = await _sandbox_conn()
        await conn.execute(
            "DELETE FROM endorsements WHERE evidence_hash = 'hash_given6_1'")
        await conn.close()
    asyncio.run(run())
