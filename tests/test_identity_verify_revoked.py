"""GET /identity/verify/<did> and GET /identity/badge/<did> for a revoked DID.

Until 2026-10-07 both answered `verified: true` for every revoked DID, because
neither query read `agents.revoked_at`. Revocation is the row update
`UPDATE agents SET revoked_at = now(), revocation_reason = ...`; this suite
makes exactly that update on a test agent it created itself and asks both
endpoints. Needs the API rig in tests/conftest.py (sandbox database).
"""


async def _revoke(did: str, reason: str) -> None:
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE agents SET revoked_at = now(), revocation_reason = $1 WHERE did = $2",
            reason, did,
        )


async def test_verify_says_true_for_a_live_did(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=100)
    r = await async_client.get(f"/identity/verify/{did}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True
    assert body["revoked"] is None
    assert body["withheld"] is False


async def test_verify_says_false_for_a_revoked_did(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=100)
    await _revoke(did, "test: key compromised")
    r = await async_client.get(f"/identity/verify/{did}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is False
    # A negative finding, not an absent one: withheld stays false.
    assert body["withheld"] is False
    assert body["revoked"]["reason"] == "test: key compromised"
    assert body["revoked"]["at"]


async def test_badge_says_true_for_a_live_did(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=100)
    r = await async_client.get(f"/identity/badge/{did}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True
    assert body["revoked"] is None


async def test_badge_says_false_for_a_revoked_did(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=100)
    await _revoke(did, "test: decommissioned")
    r = await async_client.get(f"/identity/badge/{did}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is False
    assert body["grade"] == "REVOKED"
    assert body["tier"] == "none"
    assert body["trust_score"] is None
    assert body["revoked"]["reason"] == "test: decommissioned"
