"""1a.1 — object-level ownership on the compliance surfaces.

Before this fix, any valid API-key holder could act on ANY DID: mint a
MolTrust-signed conformity VC for someone else's subject_did (impersonation),
poison any DID's assessment/incident history, or read any DID's compliance
report (IDOR). These tests pin the fix enforced by
``app.main._require_did_owner_or_admin``:

  * caller whose API key resolves to the DID (owner), or the admin key  -> allowed
  * any other caller                                                    -> uniform 403
  * a non-existent DID, for a non-owner                                 -> the SAME 403

The last point matters: a non-owner must not be able to tell "exists but not
yours" from "doesn't exist" — otherwise the endpoint is a DID-enumeration oracle.

Integration tests via the async_client + credit_test_agent fixtures (sandbox DB).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Annex-V mandatory fields minus subject_did (added per-test).
_DECL_BODY = {
    "ai_system_name": "ACME Vision", "ai_system_reference": "model-v1",
    "provider_name": "ACME Ltd", "provider_address": "1 EU Street",
    "place_of_issue": "Berlin", "signatory_name": "Lars Kroehl",
    "signatory_function": "CEO", "on_behalf_of": "ACME Ltd",
}


# --- writes: assess / declaration / incident --------------------------------

async def test_assess_rejects_foreign_did(async_client, credit_test_agent):
    _, key_a = await credit_test_agent()
    did_b, _ = await credit_test_agent()
    resp = await async_client.post(
        "/compliance/assess", headers={"X-API-Key": key_a},
        json={"did": did_b, "use_case": "x", "intended_purpose": "y"})
    assert resp.status_code == 403, resp.text[:200]


async def test_assess_without_did_stays_open(async_client, credit_test_agent):
    # No subject DID => stateless classification, nothing is persisted, so no
    # ownership is required and the endpoint stays usable as a pure classifier.
    _, key_a = await credit_test_agent()
    resp = await async_client.post(
        "/compliance/assess", headers={"X-API-Key": key_a},
        json={"use_case": "x", "intended_purpose": "y"})
    assert resp.status_code == 200, resp.text[:200]


async def test_assess_allows_own_did(async_client, credit_test_agent):
    did_a, key_a = await credit_test_agent()
    resp = await async_client.post(
        "/compliance/assess", headers={"X-API-Key": key_a},
        json={"did": did_a, "use_case": "x", "intended_purpose": "y"})
    assert resp.status_code == 200, resp.text[:200]


async def test_declaration_rejects_foreign_subject(async_client, credit_test_agent):
    # The impersonation case: A must not mint a MolTrust-signed VC for B's DID.
    # The 403 fires before issue_credential, so nothing is signed or persisted.
    _, key_a = await credit_test_agent()
    did_b, _ = await credit_test_agent()
    resp = await async_client.post(
        "/compliance/declaration", headers={"X-API-Key": key_a},
        json={"subject_did": did_b, **_DECL_BODY})
    assert resp.status_code == 403, resp.text[:200]


async def test_incident_rejects_foreign_did(async_client, credit_test_agent):
    _, key_a = await credit_test_agent()
    did_b, _ = await credit_test_agent()
    resp = await async_client.post(
        "/compliance/incident", headers={"X-API-Key": key_a},
        json={"did": did_b, "category": "death", "severity": "critical",
              "description": "unrelated agent"})
    assert resp.status_code == 403, resp.text[:200]


# --- read: report -----------------------------------------------------------

async def test_report_rejects_foreign_did(async_client, credit_test_agent):
    _, key_a = await credit_test_agent()
    did_b, _ = await credit_test_agent()
    resp = await async_client.get(
        f"/compliance/report/{did_b}", headers={"X-API-Key": key_a})
    assert resp.status_code == 403, resp.text[:200]


async def test_report_allows_own_did(async_client, credit_test_agent):
    did_a, key_a = await credit_test_agent()
    resp = await async_client.get(
        f"/compliance/report/{did_a}?format=json", headers={"X-API-Key": key_a})
    assert resp.status_code == 200, resp.text[:200]


async def test_report_nonexistent_is_same_403_no_oracle(async_client, credit_test_agent):
    # A non-owner must not distinguish "exists but not yours" from "doesn't
    # exist": identical status AND body — no enumeration oracle.
    _, key_a = await credit_test_agent()
    did_b, _ = await credit_test_agent()
    foreign = await async_client.get(
        f"/compliance/report/{did_b}", headers={"X-API-Key": key_a})
    ghost = await async_client.get(
        "/compliance/report/did:moltrust:" + "f" * 16, headers={"X-API-Key": key_a})
    assert foreign.status_code == 403 and ghost.status_code == 403
    assert foreign.json() == ghost.json()


# --- admin bypass -----------------------------------------------------------

async def test_admin_key_bypasses_ownership(async_client, credit_test_agent, monkeypatch):
    from app import credentials as _cred
    from nacl.signing import SigningKey
    _cred.get_signing_key = lambda: SigningKey(bytes.fromhex("22" * 32))
    monkeypatch.setenv("ADMIN_KEY", "test-admin-secret-1a1")
    _, key_a = await credit_test_agent()
    did_b, _ = await credit_test_agent()
    from app.main import db_pool
    try:
        resp = await async_client.post(
            "/compliance/declaration",
            headers={"X-API-Key": key_a, "x-admin-key": "test-admin-secret-1a1"},
            json={"subject_did": did_b, **_DECL_BODY})
        assert resp.status_code == 200, resp.text[:200]
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM credentials WHERE subject_did = $1", did_b)
