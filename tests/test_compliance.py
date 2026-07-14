"""EU AI Act compliance endpoints (Sprint 1: feat/compliance-core).

Two layers:
  * Pure-unit tests of the classification engine / Annex-V builder / report /
    pricing — no DB, run anywhere.
  * Integration tests for POST /compliance/assess, POST /compliance/declaration,
    GET /compliance/report/{did} — Happy path + validation error + auth — via the
    async_client + credit_test_agent fixtures (sandbox DB).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import compliance as C  # noqa: E402

_TEST_SEED_HEX = "22" * 32


def _install_test_signing_key():
    from nacl.signing import SigningKey
    from app import credentials as _cred
    test_sk = SigningKey(bytes.fromhex(_TEST_SEED_HEX))
    _cred.get_signing_key = lambda: test_sk


# ---------------------------------------------------------------------------
# Unit — classification engine (spec-fakten §Classification logic)
# ---------------------------------------------------------------------------
def test_prohibited_on_structured_flag():
    r = C.classify(use_case="rank citizens", intended_purpose="score",
                   prohibited_flags=["social_scoring"])
    assert r["risk_tier"] == C.TIER_PROHIBITED
    assert any("Art 5(1)(c)" in m["provision"] for m in r["matched_provisions"])


def test_prohibited_keyword_is_only_a_note_not_a_verdict():
    # Free-text alone must NOT force PROHIBITED — only a structured flag does.
    r = C.classify(use_case="social scoring research paper", intended_purpose="study")
    assert r["risk_tier"] != C.TIER_PROHIBITED
    assert any("Art 5(1)" in n for n in r["notes"])


def test_high_risk_annex_i_product_route():
    r = C.classify(use_case="x", intended_purpose="y",
                   is_annex_i_safety_component=True, requires_third_party_conformity=True)
    assert r["risk_tier"] == C.TIER_HIGH
    assert any("Art 6(1)" in m["provision"] for m in r["matched_provisions"])


def test_high_risk_annex_iii_explicit_area():
    r = C.classify(use_case="x", intended_purpose="y", annex_iii_area=4)
    assert r["risk_tier"] == C.TIER_HIGH
    assert r["annex_iii_area"] == 4
    arts = {o["article"] for o in r["obligations"]}
    assert {"Art 9", "Art 12", "Art 47", "Art 73"} <= arts


def test_derogation_downgrades_to_limited():
    r = C.classify(use_case="x", intended_purpose="y", annex_iii_area=4,
                   derogation_claim="narrow_procedural")
    assert r["risk_tier"] == C.TIER_LIMITED
    assert r["derogation_claimed"] == "narrow_procedural"
    assert any("Art 6(4)" in o["provision"] for o in r["obligations"])


def test_profiling_blocks_derogation():
    r = C.classify(use_case="x", intended_purpose="y", annex_iii_area=4,
                   derogation_claim="narrow_procedural", performs_profiling=True)
    assert r["risk_tier"] == C.TIER_HIGH  # Art 6(3) last subpara: profiling => always high
    assert any("profiling" in n.lower() for n in r["notes"])


def test_keyword_routes_to_annex_iii_area():
    r = C.classify(use_case="credit scoring for consumer loans",
                   intended_purpose="assess creditworthiness")
    assert r["risk_tier"] == C.TIER_HIGH
    assert r["annex_iii_area"] == 5


def test_limited_transparency_signal():
    r = C.classify(use_case="support bot", intended_purpose="chat", interacts_with_humans=True)
    assert r["risk_tier"] == C.TIER_LIMITED
    assert any("Art 50" in o["provision"] for o in r["obligations"])


def test_minimal_residual():
    r = C.classify(use_case="internal spellchecker", intended_purpose="fix typos")
    assert r["risk_tier"] == C.TIER_MINIMAL
    assert any("Art 95" in o["provision"] for o in r["obligations"])


def test_result_always_carries_pins_and_disclaimer():
    r = C.classify(use_case="x", intended_purpose="y", annex_iii_area=2)
    assert r["disclaimer"]
    assert r["spec_source"].startswith("docs/spec-fakten/")
    assert all("provision" in m for m in r["matched_provisions"])


# ---------------------------------------------------------------------------
# Unit — Annex V declaration builder (all 8 mandatory fields)
# ---------------------------------------------------------------------------
def test_declaration_has_all_annex_v_fields():
    d = C.build_declaration_claims(
        ai_system_name="ACME Vision", ai_system_reference="model-v1",
        provider_name="ACME Ltd", provider_address="1 EU Street",
        processes_personal_data=True, harmonised_standards=["EN ISO/IEC 42001"],
        place_of_issue="Berlin", signatory_name="Lars Kroehl",
        signatory_function="CEO", on_behalf_of="ACME Ltd")
    for k in ("aiSystem", "provider", "soleResponsibilityStatement", "conformityStatement",
              "dataProtectionStatement", "harmonisedStandards", "issuance"):
        assert k in d, f"Annex V field missing: {k}"
    assert d["annexVComplete"] is True
    assert d["dataProtectionStatement"] and "2016/679" in d["dataProtectionStatement"]
    assert d["issuance"]["signatory"]["name"] == "Lars Kroehl"


def test_declaration_gdpr_statement_absent_when_no_personal_data():
    d = C.build_declaration_claims(
        ai_system_name="A", ai_system_reference="r", provider_name="P", provider_address="addr",
        processes_personal_data=False, place_of_issue="Berlin", signatory_name="LK",
        signatory_function="CEO", on_behalf_of="P")
    assert d["dataProtectionStatement"] is None


# ---------------------------------------------------------------------------
# Unit — report render + pricing map
# ---------------------------------------------------------------------------
def test_report_html_renders_with_tier_badge():
    a = C.classify(use_case="cv screening for hiring", intended_purpose="filter")
    h = C.render_report_html(
        did="did:moltrust:" + "a" * 16,
        identity={"display_name": "ACME", "agent_class": "autonomous",
                  "agent_framework": None, "publisher": None},
        assessment=a, declarations=[], trust_score={"score": 4.2, "total_ratings": 3},
        audit_summary={"credentials_total": 1})
    assert "<!doctype html>" in h.lower()
    assert "tier-high" in h
    assert "did:moltrust:" + "a" * 16 in h


def test_report_html_escapes_input():
    h = C.render_report_html(
        did="did:moltrust:" + "b" * 16,
        identity={"display_name": "<script>x</script>", "agent_class": None,
                  "agent_framework": None, "publisher": None},
        assessment=None, declarations=[], trust_score=None, audit_summary={})
    assert "<script>x</script>" not in h
    assert "&lt;script&gt;" in h


def test_pricing_map_prices_compliance_like_vc_issuance():
    from app.credits import get_endpoint_cost
    assert get_endpoint_cost("POST", "/compliance/assess") == 2
    assert get_endpoint_cost("POST", "/compliance/declaration") == 2
    assert get_endpoint_cost("GET", "/compliance/report/did:moltrust:" + "a" * 16) == 1


# ---------------------------------------------------------------------------
# Integration — endpoints (Happy path + validation + auth). Need sandbox DB.
# ---------------------------------------------------------------------------
async def test_assess_happy_path(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/compliance/assess",
        headers={"X-API-Key": api_key},
        json={"did": did, "use_case": "cv screening for hiring",
              "intended_purpose": "filter job applications", "annex_iii_area": 4},
    )
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["risk_tier"] == "high"
    assert body["did"] == did
    assert any("Annex III" in m["reason"] for m in body["matched_provisions"])


async def test_assess_validation_error(async_client, credit_test_agent):
    _, api_key = await credit_test_agent(balance=1000)
    # missing required use_case
    resp = await async_client.post(
        "/compliance/assess", headers={"X-API-Key": api_key},
        json={"intended_purpose": "x"})
    assert resp.status_code == 422


async def test_assess_requires_api_key(async_client):
    resp = await async_client.post(
        "/compliance/assess",
        json={"use_case": "x", "intended_purpose": "y"})
    assert resp.status_code in (401, 403, 422)


async def test_declaration_happy_path(async_client, credit_test_agent):
    _install_test_signing_key()
    did, api_key = await credit_test_agent(balance=1000)
    from app.main import db_pool
    try:
        resp = await async_client.post(
            "/compliance/declaration",
            headers={"X-API-Key": api_key},
            json={"subject_did": did, "ai_system_name": "ACME Vision",
                  "ai_system_reference": "model-v1", "provider_name": "ACME Ltd",
                  "provider_address": "1 EU Street", "processes_personal_data": True,
                  "place_of_issue": "Berlin", "signatory_name": "Lars Kroehl",
                  "signatory_function": "CEO", "on_behalf_of": "ACME Ltd", "anchor": True},
        )
        assert resp.status_code == 200, resp.text[:300]
        vc = resp.json()["declaration"]
        assert "MolTrustConformityDeclaration" in vc["type"]
        assert vc["credentialSubject"]["annexVComplete"] is True
        assert vc["proof"]["proofValue"]
        assert resp.json()["anchoring"]["commitment_sha256"]
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM credentials WHERE subject_did = $1", did)


async def test_declaration_validation_error(async_client, credit_test_agent):
    _, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.post(
        "/compliance/declaration", headers={"X-API-Key": api_key},
        json={"subject_did": "did:moltrust:" + "a" * 16})  # missing mandatory fields
    assert resp.status_code == 422


async def test_declaration_requires_api_key(async_client):
    resp = await async_client.post(
        "/compliance/declaration",
        json={"subject_did": "did:moltrust:" + "a" * 16, "ai_system_name": "A",
              "ai_system_reference": "r", "provider_name": "P", "provider_address": "a",
              "place_of_issue": "B", "signatory_name": "S", "signatory_function": "F",
              "on_behalf_of": "P"})
    assert resp.status_code in (401, 403, 422)


async def test_report_happy_path_html(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.get(
        f"/compliance/report/{did}", headers={"X-API-Key": api_key})
    assert resp.status_code == 200, resp.text[:300]
    assert "text/html" in resp.headers.get("content-type", "")
    assert did in resp.text


async def test_report_json_format(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.get(
        f"/compliance/report/{did}?format=json", headers={"X-API-Key": api_key})
    assert resp.status_code == 200
    assert resp.json()["did"] == did
    assert "audit_summary" in resp.json()


async def test_report_not_found_for_admin(async_client, credit_test_agent, monkeypatch):
    # The 404 branch stays reachable for an AUTHORIZED caller (admin here). A
    # non-owner now gets a uniform 403 with no found/not-found distinction —
    # see tests/test_compliance_ownership.py.
    monkeypatch.setenv("ADMIN_KEY", "test-admin-secret-report")
    _, api_key = await credit_test_agent(balance=1000)
    resp = await async_client.get(
        "/compliance/report/did:moltrust:" + "f" * 16,
        headers={"X-API-Key": api_key, "x-admin-key": "test-admin-secret-report"})
    assert resp.status_code == 404


async def test_report_requires_api_key(async_client):
    resp = await async_client.get("/compliance/report/did:moltrust:" + "a" * 16)
    assert resp.status_code in (401, 403, 422)
