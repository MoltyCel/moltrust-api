"""POST /identity/spiffe/bind needs proof of control of the SVID key (BIND-CHECK 4).

Red: no proof, a proof signed by another key, and an SVID for another SPIFFE ID
are each refused with 4xx, and nothing is stored. Green: a valid proof stores
the binding and its proof row. Unit tests for app/spiffe_proof.py at the end.

Needs the API rig in tests/conftest.py (sandbox database).
"""
import base64
import datetime
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

from app import spiffe_proof


@pytest.fixture(autouse=True)
def _no_rate_limit(app_with_lifespan):
    from app.main import limiter
    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


def make_svid(spiffe_uri: str, key=None, *, days: int = 1, start_days: int = -1):
    key = key or ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "test")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "test-ca")]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + datetime.timedelta(days=start_days))
        .not_valid_after(now + datetime.timedelta(days=start_days + days + 1))
        .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_uri)]),
                       critical=False)
        .sign(key, None if isinstance(key, ed25519.Ed25519PrivateKey) else hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


def sign(key, message: bytes) -> str:
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return base64.b64encode(key.sign(message)).decode()
    return base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode()


def _uri():
    return f"spiffe://test.local/bind/{uuid.uuid4().hex[:12]}"


async def _stored(uri):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        b = await conn.fetchval("SELECT count(*) FROM spiffe_bindings WHERE spiffe_uri = $1", uri)
        try:
            p = await conn.fetchval("SELECT count(*) FROM spiffe_binding_proofs WHERE spiffe_uri = $1", uri)
        except Exception:  # noqa: BLE001 - table not created yet means no proof row
            p = 0
    return b, p


async def _cleanup(uri):
    from app.main import db_pool
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM spiffe_bindings WHERE spiffe_uri = $1", uri)
        try:
            await conn.execute("DELETE FROM spiffe_binding_proofs WHERE spiffe_uri = $1", uri)
        except Exception:  # noqa: BLE001
            pass


async def _challenge(client, api_key, uri, did):
    r = await client.post("/identity/spiffe/challenge", json={"spiffe_uri": uri, "did": did},
                          headers={"X-API-Key": api_key})
    assert r.status_code == 200, r.text
    return r.json()


async def test_bind_without_proof_is_refused(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    uri = _uri()
    r = await async_client.post("/identity/spiffe/bind", json={"spiffe_uri": uri, "did": did},
                                headers={"X-API-Key": api_key})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "svid_proof_required"
    assert await _stored(uri) == (0, 0)


async def test_bind_with_foreign_proof_is_refused(async_client, credit_test_agent):
    """The SVID is right, the signature comes from a key the caller does not hold
    for it; and separately, a correctly signed SVID for another SPIFFE ID."""
    did, api_key = await credit_test_agent()
    uri = _uri()
    ch = await _challenge(async_client, api_key, uri, did)
    _, svid = make_svid(uri)
    other_key = ec.generate_private_key(ec.SECP256R1())
    r = await async_client.post("/identity/spiffe/bind", json={
        "spiffe_uri": uri, "did": did, "nonce": ch["nonce"], "svid": svid,
        "signature": sign(other_key, ch["message"].encode())}, headers={"X-API-Key": api_key})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "svid_proof_invalid"
    assert await _stored(uri) == (0, 0)

    key2, svid_other_id = make_svid(_uri())
    r = await async_client.post("/identity/spiffe/bind", json={
        "spiffe_uri": uri, "did": did, "nonce": ch["nonce"], "svid": svid_other_id,
        "signature": sign(key2, ch["message"].encode())}, headers={"X-API-Key": api_key})
    assert r.status_code == 403, r.text
    assert "URI SAN" in r.json()["detail"]["message"]
    assert await _stored(uri) == (0, 0)


async def test_bind_with_valid_proof_is_stored(async_client, credit_test_agent):
    did, api_key = await credit_test_agent()
    uri = _uri()
    try:
        ch = await _challenge(async_client, api_key, uri, did)
        key, svid = make_svid(uri)
        r = await async_client.post("/identity/spiffe/bind", json={
            "spiffe_uri": uri, "did": did, "nonce": ch["nonce"], "svid": svid,
            "signature": sign(key, ch["message"].encode())}, headers={"X-API-Key": api_key})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "bound" and len(r.json()["svid_sha256"]) == 64
        assert await _stored(uri) == (1, 1)
        look = await async_client.get(f"/identity/spiffe/{uri}")
        assert look.status_code == 200 and look.json()["moltrust_did"] == did
    finally:
        await _cleanup(uri)


# --- unit: app/spiffe_proof.py ---------------------------------------------

SECRET = "unit-secret"


def test_nonce_is_bound_to_uri_and_did_and_expires():
    uri, did = "spiffe://a.example/w", "did:moltrust:0123456789abcdef"
    n = spiffe_proof.issue_nonce(SECRET, uri, did, now=1000)
    spiffe_proof.check_nonce(SECRET, uri, did, n, now=1100)
    with pytest.raises(spiffe_proof.SpiffeProofError):
        spiffe_proof.check_nonce(SECRET, uri, "did:moltrust:ffffffffffffffff", n, now=1100)
    with pytest.raises(spiffe_proof.SpiffeProofError):
        spiffe_proof.check_nonce(SECRET, uri, did, n, now=1000 + spiffe_proof.NONCE_MAX_AGE + 1)


@pytest.mark.parametrize("keytype", ["ec", "ed25519"])
def test_proof_accepts_the_svid_key(keytype):
    uri, did = "spiffe://a.example/w", "did:moltrust:0123456789abcdef"
    key = ed25519.Ed25519PrivateKey.generate() if keytype == "ed25519" else None
    key, svid = make_svid(uri, key)
    n = spiffe_proof.issue_nonce(SECRET, uri, did)
    out = spiffe_proof.verify_svid_proof(spiffe_uri=uri, did=did, nonce=n, svid_pem=svid,
                                         signature_b64=sign(key, spiffe_proof.binding_message(uri, did, n)),
                                         secret=SECRET)
    assert len(out["svid_sha256"]) == 64


def test_proof_refuses_an_expired_svid():
    uri, did = "spiffe://a.example/w", "did:moltrust:0123456789abcdef"
    key, svid = make_svid(uri, start_days=-10, days=1)
    n = spiffe_proof.issue_nonce(SECRET, uri, did)
    with pytest.raises(spiffe_proof.SpiffeProofError, match="validity"):
        spiffe_proof.verify_svid_proof(spiffe_uri=uri, did=did, nonce=n, svid_pem=svid,
                                       signature_b64=sign(key, spiffe_proof.binding_message(uri, did, n)),
                                       secret=SECRET)
