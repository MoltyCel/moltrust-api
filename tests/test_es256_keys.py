"""ES256 (P-256) next to Ed25519 — KEY-AGILITY, 20.11.2026.

Covers: the key is off by default and fails closed when enabled without a
usable key; sign/verify round-trip; the key fixes the algorithm; the EC-only
JWKS route; the did:web document gains #key-p256 only with the flag and keeps
Ed25519 first; the Ed25519 registry key and its JWK are untouched.
"""
import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app import es256_keys, registry_keys

# Test scalar, not a production key. Any 32-byte value below the curve order.
TEST_SCALAR = "11" * 32


@pytest.fixture
def es256_on(monkeypatch):
    monkeypatch.setenv("KEY_AGILITY_ES256_ENABLED", "true")
    monkeypatch.setenv("MOLTRUST_ES256_PRIVATE_KEY", TEST_SCALAR)


@pytest.fixture
def es256_off(monkeypatch):
    monkeypatch.delenv("KEY_AGILITY_ES256_ENABLED", raising=False)
    monkeypatch.delenv("MOLTRUST_ES256_PRIVATE_KEY", raising=False)


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_off_by_default(es256_off):
    assert es256_keys.enabled() is False
    with pytest.raises(es256_keys.Es256Unavailable):
        es256_keys.get_private_key()


def test_key_set_but_flag_off_still_refuses(monkeypatch, es256_off):
    monkeypatch.setenv("MOLTRUST_ES256_PRIVATE_KEY", TEST_SCALAR)
    with pytest.raises(es256_keys.Es256Unavailable):
        es256_keys.get_private_key()


@pytest.mark.parametrize("value", ["", "zz" * 32, "11" * 31, "00" * 32, "ff" * 32])
def test_flag_on_bad_key_fails_closed(monkeypatch, value):
    monkeypatch.setenv("KEY_AGILITY_ES256_ENABLED", "true")
    monkeypatch.setenv("MOLTRUST_ES256_PRIVATE_KEY", value)
    with pytest.raises(es256_keys.Es256Unavailable):
        es256_keys.get_private_key()


def test_jwk_shape(es256_on):
    jwk = es256_keys.get_public_jwk()
    assert jwk["kty"] == "EC" and jwk["crv"] == "P-256" and jwk["alg"] == "ES256"
    assert jwk["kid"] == es256_keys.ES256_KID != registry_keys.REGISTRY_KID
    assert len(_b64d(jwk["x"])) == 32 and len(_b64d(jwk["y"])) == 32
    assert "d" not in jwk


def test_sign_verify_round_trip(es256_on):
    token = es256_keys.sign_jws({"v": 1, "hello": "world"})
    header = json.loads(_b64d(token.split(".")[0]))
    assert header == {"alg": "ES256", "typ": "JWT", "kid": es256_keys.ES256_KID}
    assert len(_b64d(token.split(".")[2])) == 64  # raw r||s, not DER
    assert es256_keys.verify_jws(token, es256_keys.get_public_jwk()) == {"v": 1, "hello": "world"}


def test_tampered_payload_is_refused(es256_on):
    h, _, s = es256_keys.sign_jws({"amount": "0.01"}).split(".")
    forged = base64.urlsafe_b64encode(b'{"amount":"0.00"}').rstrip(b"=").decode()
    with pytest.raises(ValueError):
        es256_keys.verify_jws(f"{h}.{forged}.{s}", es256_keys.get_public_jwk())


def test_header_naming_another_alg_is_refused(es256_on):
    token = es256_keys.sign_jws({"x": 1})
    _, p, s = token.split(".")
    bad_h = base64.urlsafe_b64encode(
        json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": es256_keys.ES256_KID}).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(ValueError, match="alg"):
        es256_keys.verify_jws(f"{bad_h}.{p}.{s}", es256_keys.get_public_jwk())


def test_ed25519_jwk_is_not_accepted_by_the_es256_verifier(es256_on):
    token = es256_keys.sign_jws({"x": 1})
    with pytest.raises(ValueError):
        es256_keys.verify_jws(token, registry_keys.get_public_jwk())


def test_multikey_is_p256_multicodec(es256_on):
    mb = es256_keys.get_public_multikey()
    assert mb.startswith("zDn")  # base58btc of 0x8024 + compressed point
    alphabet = es256_keys._B58
    n = 0
    for ch in mb[1:]:
        n = n * 58 + alphabet.index(ch)
    raw = n.to_bytes(35, "big")
    assert raw[:2] == bytes([0x80, 0x24])
    point = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw[2:])
    jwk = es256_keys.get_public_jwk()
    assert point.public_numbers().x == int.from_bytes(_b64d(jwk["x"]), "big")


def test_registry_ed25519_jwk_unchanged(es256_on):
    jwk = registry_keys.get_public_jwk()
    assert (jwk["kty"], jwk["crv"], jwk["alg"], jwk["kid"]) == (
        "OKP", "Ed25519", "EdDSA", "moltrust-registry-2026-v1")


def _did_doc():
    from app.main import _build_issuer_did_document
    return _build_issuer_did_document()


def test_did_document_without_flag_is_ed25519_only(es256_off):
    doc = _did_doc()
    assert [vm["id"].split("#")[1] for vm in doc["verificationMethod"]][:2] == ["key-ed25519", "key-1"]
    assert not any(vm["id"].endswith("#key-p256") for vm in doc["verificationMethod"])
    assert "https://w3id.org/security/multikey/v1" not in doc["@context"]


def test_did_document_with_flag_adds_p256_after_ed25519(es256_on):
    doc = _did_doc()
    ids = [vm["id"].split("#")[1] for vm in doc["verificationMethod"]]
    assert ids[:2] == ["key-ed25519", "key-1"]
    assert "key-p256" in ids
    vm = next(v for v in doc["verificationMethod"] if v["id"].endswith("#key-p256"))
    assert vm["type"] == "Multikey" and vm["publicKeyMultibase"].startswith("zDn")
    assert doc["assertionMethod"][0].endswith("#key-ed25519")
    assert any(a.endswith("#key-p256") for a in doc["assertionMethod"])
    assert not any(a.endswith("#key-p256") for a in doc["authentication"])


def test_did_document_flag_on_broken_key_omits_method(monkeypatch):
    monkeypatch.setenv("KEY_AGILITY_ES256_ENABLED", "true")
    monkeypatch.setenv("MOLTRUST_ES256_PRIVATE_KEY", "not-hex")
    doc = _did_doc()
    assert not any(vm["id"].endswith("#key-p256") for vm in doc["verificationMethod"])


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.es256_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_jwks_route_404_when_off(es256_off):
    assert _client().get("/.well-known/jwks-es256.json").status_code == 404


def test_jwks_route_ec_only_when_on(es256_on):
    r = _client().get("/.well-known/jwks-es256.json")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert [k["kty"] for k in keys] == ["EC"]
    assert len(r.content) < 5120  # AWS limit for a quote-signing JWKS


def test_jwks_route_503_when_on_but_key_missing(monkeypatch):
    monkeypatch.setenv("KEY_AGILITY_ES256_ENABLED", "true")
    monkeypatch.delenv("MOLTRUST_ES256_PRIVATE_KEY", raising=False)
    r = _client().get("/.well-known/jwks-es256.json")
    assert r.status_code == 503 and r.json()["error"] == "es256_key_unavailable"
