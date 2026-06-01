"""Tests — POST /vc/aae/submit durch die VOLLE Middleware-Kette (async TestClient).

Lehre aus #99-101: write-Endpoint-Tests müssen durch die Middleware laufen
(content_filter_middleware/scrub_secrets), nicht nur direkter Funktions-Call —
sonst bleibt Middleware-Drift (z.B. Maskierung von aae_ref) unsichtbar.

aae_envelopes ist append-only: Endpoint-Tests hinterlassen test-markierte Zeilen
(issuer_did='did:moltrust:test_issuer', aae_id-Prefix 'test:aae:') — konsistent
mit der append-only-Konvention (credit_transactions/IPR lassen Testzeilen stehen).
Eindeutige aae_id pro Run via uuid -> keine Cross-Run-Kollision.
"""
import uuid


def _envelope(aae_id=None, scope=None):
    aae_id = aae_id or f"test:aae:{uuid.uuid4().hex[:12]}"
    return {
        "aae_id": aae_id,
        "issuer_did": "did:moltrust:test_issuer",
        "signature": "testsig",
        "mandate": {"scope": scope if scope is not None else ["payments:write"]},
        "constraints": [
            {"type": "max_transaction_value", "value": 500, "currency": "USD", "required": True}
        ],
        "validity": {"not_before": "2026-06-01T00:00:00Z", "not_after": "2026-06-01T18:00:00Z"},
        "aae_version": "1.0",
        "taxonomy_version": "1.0",
    }


async def test_submit_success_and_aae_ref_not_scrubbed(async_client, credit_test_agent):
    _did, api_key = await credit_test_agent()
    env = _envelope()
    r = await async_client.post("/vc/aae/submit", json=env, headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["stored"] is True
    assert data["aae_id"] == env["aae_id"]
    # aae_ref muss VOLLSTÄNDIG durch content_filter_middleware/scrub_secrets kommen.
    assert data["aae_ref"].startswith("sha256:")
    assert len(data["aae_ref"]) == len("sha256:") + 64
    assert "[REDACTED]" not in data["aae_ref"]


async def test_auth_missing_401(async_client):
    r = await async_client.post("/vc/aae/submit", json=_envelope())
    assert r.status_code == 401


async def test_invalid_shape_422(async_client, credit_test_agent):
    _did, api_key = await credit_test_agent()
    env = _envelope()
    env["constraints"] = [{"value": 1}]  # missing 'type' -> EnvelopeValidationError -> 422
    r = await async_client.post("/vc/aae/submit", json=env, headers={"X-API-Key": api_key})
    assert r.status_code == 422, r.text


async def test_single_use_duplicate_409(async_client, credit_test_agent):
    _did, api_key = await credit_test_agent()
    aae_id = f"test:aae:{uuid.uuid4().hex[:12]}"
    env1 = _envelope(aae_id=aae_id, scope=["x"])
    r1 = await async_client.post("/vc/aae/submit", json=env1, headers={"X-API-Key": api_key})
    assert r1.status_code == 200, r1.text
    # gleicher aae_id + gleicher scope, anderer Inhalt (validity) -> anderer aae_ref (PK),
    # aber single_use-Kollision auf (aae_id, digest(scope_canonical)) -> 409
    env2 = _envelope(aae_id=aae_id, scope=["x"])
    env2["validity"] = {"not_before": "2026-06-02T00:00:00Z", "not_after": "2026-06-02T18:00:00Z"}
    r2 = await async_client.post("/vc/aae/submit", json=env2, headers={"X-API-Key": api_key})
    assert r2.status_code == 409, r2.text
