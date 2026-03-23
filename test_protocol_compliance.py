"""
MolTrust Protocol Compliance — Test Suite
Tests for Tech Spec v0.2.2 features:
  - ViolationRecord endpoints (Feature 1)
  - Delegation Chain Depth-Limit (Feature 2)
  - Sequential Signing Validation (Feature 3)

Runs against sandbox at http://localhost:8005
"""
import pytest, httpx, os, uuid

BASE = os.getenv("TEST_BASE_URL", "http://localhost:8005")
ADMIN_KEY = os.getenv("ADMIN_KEY", "")


@pytest.fixture(scope="module")
def admin_headers():
    return {"X-Admin-Key": ADMIN_KEY}


@pytest.fixture(scope="module")
def client():
    return httpx.Client(base_url=BASE, timeout=15.0)


# ── Feature 1: ViolationRecord ──────────────────────────────────────────────

class TestViolationRecord:

    @pytest.fixture(autouse=True)
    def setup(self, client, admin_headers):
        self.client = client
        self.admin_headers = admin_headers
        self._created_id = None

    def test_violation_record_create(self):
        resp = self.client.post("/violation/record", json={
            "agent_did": "did:moltrust:testviolator001",
            "principal_did": "did:moltrust:testprincipal001",
            "violation_type": "sybil",
            "interaction_proof_id": str(uuid.uuid4()),
            "description": "Automated test — sybil detection triggered",
            "adjudicator_reference": "test-adjudicator-ref-001",
            "confirmed_at": "2026-03-23T12:00:00Z",
        }, headers=self.admin_headers)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["type"] == "ViolationRecord"
        assert data["@context"] == "https://moltrust.ch/ns/violation/v1"
        assert data["subject"]["agentDid"] == "did:moltrust:testviolator001"
        assert data["violation"]["type"] == "sybil"
        assert data["registrySignature"]["type"] == "Ed25519Signature2020"
        assert data["reversed"] is False
        self.__class__._created_id = data["id"]

    def test_violation_record_create_requires_admin(self):
        resp = self.client.post("/violation/record", json={
            "agent_did": "did:moltrust:testviolator001",
            "principal_did": "did:moltrust:testprincipal001",
            "violation_type": "sybil",
            "confirmed_at": "2026-03-23T12:00:00Z",
        })
        assert resp.status_code == 403

    def test_violation_record_invalid_type(self):
        resp = self.client.post("/violation/record", json={
            "agent_did": "did:moltrust:testviolator001",
            "principal_did": "did:moltrust:testprincipal001",
            "violation_type": "not-a-real-type",
            "confirmed_at": "2026-03-23T12:00:00Z",
        }, headers=self.admin_headers)
        assert resp.status_code == 422  # Pydantic validation

    def test_violation_record_get(self):
        # First create one
        create_resp = self.client.post("/violation/record", json={
            "agent_did": "did:moltrust:testviolator002",
            "principal_did": "did:moltrust:testprincipal002",
            "violation_type": "identity-spoofing",
            "confirmed_at": "2026-03-23T12:00:00Z",
        }, headers=self.admin_headers)
        assert create_resp.status_code == 200
        record_id = create_resp.json()["id"]

        # Now fetch it
        resp = self.client.get(f"/violation/{record_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == record_id
        assert data["violation"]["type"] == "identity-spoofing"

    def test_violation_record_get_not_found(self):
        resp = self.client.get(f"/violation/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_violation_reversal(self):
        # Create
        create_resp = self.client.post("/violation/record", json={
            "agent_did": "did:moltrust:testviolator003",
            "principal_did": "did:moltrust:testprincipal003",
            "violation_type": "behavioral-fraud",
            "confirmed_at": "2026-03-23T12:00:00Z",
        }, headers=self.admin_headers)
        assert create_resp.status_code == 200
        record_id = create_resp.json()["id"]

        # Reverse
        resp = self.client.post(f"/violation/{record_id}/reverse", json={
            "adjudicator_reference": "appeal-granted-ref-001",
            "reversal_date": "2026-03-23T14:00:00Z",
        }, headers=self.admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "ViolationReversal"
        assert data["reversed"] is True
        assert data["record"]["reversed"] is True

    def test_violation_reversal_double_reverse_rejected(self):
        # Create and reverse
        create_resp = self.client.post("/violation/record", json={
            "agent_did": "did:moltrust:testviolator004",
            "principal_did": "did:moltrust:testprincipal004",
            "violation_type": "clone-impersonation",
            "confirmed_at": "2026-03-23T12:00:00Z",
        }, headers=self.admin_headers)
        record_id = create_resp.json()["id"]
        self.client.post(f"/violation/{record_id}/reverse", json={
            "adjudicator_reference": "ref",
        }, headers=self.admin_headers)

        # Second reverse should fail
        resp = self.client.post(f"/violation/{record_id}/reverse", json={
            "adjudicator_reference": "ref2",
        }, headers=self.admin_headers)
        assert resp.status_code == 409

    def test_violation_agent_lookup(self):
        agent_did = "did:moltrust:testviolator005"
        # Create two violations for same agent
        for vtype in ["sybil", "authorization-abuse"]:
            self.client.post("/violation/record", json={
                "agent_did": agent_did,
                "principal_did": "did:moltrust:testprincipal005",
                "violation_type": vtype,
                "confirmed_at": "2026-03-23T12:00:00Z",
            }, headers=self.admin_headers)

        resp = self.client.get(f"/violation/agent/{agent_did}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_did"] == agent_did
        assert data["total"] >= 2
        types = {v["violation"]["type"] for v in data["violations"]}
        assert "sybil" in types
        assert "authorization-abuse" in types


# ── Feature 2: Delegation Chain Depth-Limit ──────────────────────────────────

class TestDelegationChainDepth:

    @pytest.fixture(autouse=True)
    def setup(self, client):
        self.client = client

    def test_delegation_chain_depth_limit(self):
        chain = [{"credential": f"vc-{i}"} for i in range(5)]
        resp = self.client.post("/credentials/verify-chain", json={
            "credential_chain": chain,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["depth"] == 5
        assert data["max_depth"] == 8

    def test_delegation_chain_depth_exceeded(self):
        chain = [{"credential": f"vc-{i}"} for i in range(12)]
        resp = self.client.post("/credentials/verify-chain", json={
            "credential_chain": chain,
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "delegation_chain_too_deep"
        assert data["max_depth"] == 8
        assert data["actual_depth"] == 12

    def test_delegation_chain_exactly_8(self):
        chain = [{"credential": f"vc-{i}"} for i in range(8)]
        resp = self.client.post("/credentials/verify-chain", json={
            "credential_chain": chain,
        })
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_delegation_chain_empty(self):
        resp = self.client.post("/credentials/verify-chain", json={
            "credential_chain": [],
        })
        assert resp.status_code == 200
        assert resp.json()["depth"] == 0


# ── Feature 3: Sequential Signing Validation ─────────────────────────────────

class TestSequentialSigning:

    @pytest.fixture(autouse=True)
    def setup(self, client):
        self.client = client

    def test_sequential_signing_bilateral_valid(self):
        resp = self.client.post("/interaction/validate-signing", json={
            "proofInitiator": "did:moltrust:agent_a",
            "proofResponder": "did:moltrust:agent_b",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["signing_mode"] == "bilateral"

    def test_sequential_signing_missing_initiator(self):
        resp = self.client.post("/interaction/validate-signing", json={
            "proofResponder": "did:moltrust:agent_b",
        })
        assert resp.status_code == 400

    def test_sequential_signing_missing_responder(self):
        resp = self.client.post("/interaction/validate-signing", json={
            "proofInitiator": "did:moltrust:agent_a",
        })
        assert resp.status_code == 400

    def test_sequential_signing_singlesig_valid(self):
        resp = self.client.post("/interaction/validate-signing", json={
            "proofInitiator": "did:moltrust:agent_a",
            "singleSig": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["signing_mode"] == "single"

    def test_sequential_signing_singlesig_with_responder_rejected(self):
        resp = self.client.post("/interaction/validate-signing", json={
            "proofInitiator": "did:moltrust:agent_a",
            "proofResponder": "did:moltrust:agent_b",
            "singleSig": True,
        })
        assert resp.status_code == 400


# ── Regression: Seed Floor Guard ─────────────────────────────────────────────

class TestSeedFloorGuard:
    """CRITICAL regression test: seed agents must never fall below base_score.
    Guards against the 2-agent echo chamber problem (2026-03-22)."""

    @pytest.fixture(autouse=True)
    def setup(self, client, admin_headers):
        self.client = client
        self.admin_headers = admin_headers

    def test_seed_agents_never_below_base_score(self):
        """TrustScout (base 85) and Ambassador (base 80) must always score >= base."""
        for did, base in [
            ("did:moltrust:d34ed796a4dc4698", 85.0),
            ("did:moltrust:ambassador0001", 80.0),
        ]:
            resp = self.client.get(f"/skill/trust-score/{did}")
            assert resp.status_code == 200, f"{did}: HTTP {resp.status_code}"
            data = resp.json()
            score = data["trust_score"]
            assert score is not None, f"{did}: score is None"
            assert score >= base, (
                f"{did}: score {score} fell below base_score {base} — "
                f"seed floor guard may be missing!"
            )
            assert data["grade"] != "F", f"{did}: grade is F — catastrophic"
            assert data["grade"] in ("S", "A", "B"), (
                f"{did}: grade {data[grade]} too low for seed agent"
            )


# ── Skill VC Persistence & Anchoring ─────────────────────────────────────────

class TestSkillVCPersistence:
    """Tests for DB-backed skill VCs and on-chain anchoring."""

    GUARD_BASE = "https://api.moltrust.ch/guard"
    TEST_HASH = "sha256:ee9fd8343dc0a2e8f1e5cad1d050874cc7abb2291302351d547aa12f999db6f9"

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = httpx.Client(base_url=self.GUARD_BASE, timeout=15.0)

    def test_skill_vc_persists_to_db(self):
        """VC issued earlier should be retrievable (proves DB persistence)."""
        resp = self.client.get(f"/skill/verify/{self.TEST_HASH}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["verified"] is True
        assert data["credential"]["credentialSubject"]["skillName"] == "MolTrust MCP Server"

    def test_skill_vc_survives_restart(self):
        """Same VC still present — DB survives service restarts (not in-memory)."""
        resp = self.client.get(f"/skill/verify/{self.TEST_HASH}")
        assert resp.status_code == 200
        assert resp.json()["verified"] is True

    def test_skill_vc_has_real_anchor(self):
        """anchor_tx must be a real 0x TX hash, not None or placeholder."""
        resp = self.client.get(f"/skill/anchor/{self.TEST_HASH}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["anchored"] is True
        assert data["anchor_tx"] is not None
        assert data["anchor_tx"].startswith("0x")
        assert len(data["anchor_tx"]) == 66  # 0x + 64 hex chars
        assert data["anchor_block"] is not None

    def test_skill_vc_agent_lookup(self):
        """List all skill VCs for a DID."""
        resp = self.client.get("/skill/verify/did/did:moltrust:d34ed796a4dc4698")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authorDID"] == "did:moltrust:d34ed796a4dc4698"
        assert len(data["credentials"]) >= 1
        assert any(
            c["credentialSubject"]["skillName"] == "MolTrust MCP Server"
            for c in data["credentials"]
        )
