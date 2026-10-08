"""POST /identity/revoke (section 4.4) and POST /identity/rotate-key (section 4.3)
against the sandbox database. Needs the API rig in tests/conftest.py."""
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.key_rotation import rotation_payload


def _key():
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def _agent_with_key(credit_test_agent):
    from app.main import db_pool, ensure_key_history_table
    did, api_key = await credit_test_agent(balance=100)
    sk, pk = _key()
    async with db_pool.acquire() as conn:
        await ensure_key_history_table(conn)
        await conn.execute("UPDATE agents SET public_key_hex = $1 WHERE did = $2", pk.hex(), did)
    return did, api_key, sk, pk


async def _cleanup_history(did):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM agent_key_history WHERE did = $1", did)


async def test_revoke_with_the_did_in_the_body(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=100)
    r = await async_client.post("/identity/revoke", json={"did": did, "reason": "decommissioned"},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == did
    doc = (await async_client.get(f"/identity/resolve/{did}")).json()
    assert doc.get("deactivated") is True


async def test_revoke_of_another_agent_is_refused(async_client, credit_test_agent):
    did_a, key_a = await credit_test_agent(balance=100)
    did_b, _ = await credit_test_agent(balance=100)
    r = await async_client.post("/identity/revoke", json={"did": did_b},
                                headers={"X-API-Key": key_a})
    assert r.status_code == 403


async def test_a_live_agent_is_not_marked_deactivated(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=100)
    doc = (await async_client.get(f"/identity/resolve/{did}")).json()
    assert "deactivated" not in doc


async def test_rotate_key_keeps_the_old_key_as_revoked(async_client, credit_test_agent):
    did, api_key, sk, pk = await _agent_with_key(credit_test_agent)
    try:
        _, new = _key()
        sig = _b64url(sk.sign(rotation_payload(did, pk.hex(), new.hex())))
        r = await async_client.post("/identity/rotate-key", headers={"X-API-Key": api_key},
                                    json={"did": did, "new_public_key": _b64url(new), "signature": sig})
        assert r.status_code == 200, r.text
        assert r.json()["key_in_force"] == f"{did}#key-2"
        doc = (await async_client.get(f"/identity/resolve/{did}")).json()
        by_id = {m["id"]: m for m in doc["verificationMethod"]}
        assert by_id[f"{did}#key-2"]["publicKeyHex"] == new.hex()
        assert by_id[f"{did}#key-1"]["revoked"] is True
        assert "revokedDate" in by_id[f"{did}#key-1"]
        assert doc["authentication"] == [f"{did}#key-2"]
    finally:
        await _cleanup_history(did)


async def test_rotate_key_refuses_a_signature_by_the_new_key(async_client, credit_test_agent):
    did, api_key, _, pk = await _agent_with_key(credit_test_agent)
    try:
        new_sk, new = _key()
        sig = _b64url(new_sk.sign(rotation_payload(did, pk.hex(), new.hex())))
        r = await async_client.post("/identity/rotate-key", headers={"X-API-Key": api_key},
                                    json={"did": did, "new_public_key": new.hex(), "signature": sig})
        assert r.status_code == 401
    finally:
        await _cleanup_history(did)


async def test_rotate_key_refuses_a_replayed_request(async_client, credit_test_agent):
    did, api_key, sk, pk = await _agent_with_key(credit_test_agent)
    try:
        _, new = _key()
        body = {"did": did, "new_public_key": new.hex(),
                "signature": _b64url(sk.sign(rotation_payload(did, pk.hex(), new.hex())))}
        first = await async_client.post("/identity/rotate-key", headers={"X-API-Key": api_key}, json=body)
        assert first.status_code == 200, first.text
        again = await async_client.post("/identity/rotate-key", headers={"X-API-Key": api_key}, json=body)
        assert again.status_code == 401
    finally:
        await _cleanup_history(did)


async def test_rotate_key_without_a_key_on_record_is_refused(async_client, credit_test_agent):
    did, api_key = await credit_test_agent(balance=100)
    _, new = _key()
    r = await async_client.post("/identity/rotate-key", headers={"X-API-Key": api_key},
                                json={"did": did, "new_public_key": new.hex(), "signature": "AA"})
    assert r.status_code == 409


async def test_rotate_key_of_another_agent_is_refused(async_client, credit_test_agent):
    did_a, _, sk, pk = await _agent_with_key(credit_test_agent)
    _, key_b = await credit_test_agent(balance=100)
    try:
        _, new = _key()
        sig = _b64url(sk.sign(rotation_payload(did_a, pk.hex(), new.hex())))
        r = await async_client.post("/identity/rotate-key", headers={"X-API-Key": key_b},
                                    json={"did": did_a, "new_public_key": new.hex(), "signature": sig})
        assert r.status_code == 403
    finally:
        await _cleanup_history(did_a)


@pytest.mark.parametrize("new_key", ["", "00" * 31, "not a key"])
async def test_rotate_key_refuses_a_malformed_new_key(async_client, credit_test_agent, new_key):
    did, api_key = await credit_test_agent(balance=100)
    r = await async_client.post("/identity/rotate-key", headers={"X-API-Key": api_key},
                                json={"did": did, "new_public_key": new_key, "signature": "AA"})
    assert r.status_code == 400
