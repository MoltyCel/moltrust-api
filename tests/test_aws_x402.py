"""AWS Marketplace x402 entry point (AWS-X402 v2, 20.11.2026).

The payment-service double, the DPoP helper and most cases follow the seller
SDK's own tests: aws/fiat-extension-for-x402-py, tests/support.py and
tests/integration/test_x402_middleware.py at 536dddae8e (Apache-2.0,
Copyright Amazon.com, Inc. or its affiliates). Adapted to one entry point with
a price per operation, the ES256 key from app.es256_keys, and the replay hook.

Offline: the AWS JWKS fetch is replaced by the fake service's key set.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

pytest.importorskip("fiat_extension_x402")
pytest.importorskip("x402")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fiat_extension_x402 import AgenticCommerceVerifier, LocalQuoteSigner
from fiat_extension_x402.dpop import compute_jwk_thumbprint

from app import es256_keys
from app.aws_x402 import app as aws
from app.aws_x402 import offers
from app.aws_x402.stores import MemoryNonceStore, MemoryPaymentStore

SERVICE_URL = "https://api.moltrust.ch/aws/x402"
PRODUCT = "prod-testtesttest1"
ADDR = "0x" + "ab" * 20
ISSUER = "agentcore-payments.marketplace.amazonaws.com"


def _b64(n: int, length: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


def ec_key(kid, curve=None, alg="ES256"):
    curve = curve or ec.SECP256R1()
    private_key = ec.generate_private_key(curve)
    nums = private_key.public_key().public_numbers()
    size = (curve.key_size + 7) // 8
    crv = {"secp256r1": "P-256", "secp384r1": "P-384"}[curve.name]
    return private_key, {"kty": "EC", "crv": crv, "x": _b64(nums.x, size),
                         "y": _b64(nums.y, size), "kid": kid, "alg": alg}


class FakePaymentService:
    """Issues payment tokens the way AgentCore payments does (ES384)."""

    def __init__(self):
        self.private_key, self.jwk = ec_key("service-key", ec.SECP384R1(), "ES384")

    @property
    def jwks(self):
        return {"keys": [self.jwk]}

    def issue(self, quote_token, *, audience=SERVICE_URL, issuer=ISSUER,
              expires_in=300, dpop_jwk=None, payment_id=None):
        now = int(time.time())
        pid = payment_id or f"awsmp-{uuid.uuid4()}"
        claims = {"iss": issuer, "aud": audience, "sub": "123456789012", "payment_id": pid,
                  "jti": pid, "quote_token": quote_token, "iat": now, "exp": now + expires_in}
        if dpop_jwk is not None:
            claims["cnf"] = {"jkt": compute_jwk_thumbprint(dpop_jwk)}
        return jwt.encode(claims, self.private_key, algorithm="ES384",
                          headers={"kid": self.jwk["kid"]})


def dpop_proof(private_key, jwk, *, htm, htu, access_token):
    claims = {"jti": str(uuid.uuid4()), "htm": htm, "htu": htu, "iat": int(time.time()),
              "ath": base64.urlsafe_b64encode(
                  hashlib.sha256(access_token.encode("ascii")).digest()).rstrip(b"=").decode()}
    public = {k: jwk[k] for k in ("kty", "crv", "x", "y")}
    return jwt.encode(claims, private_key, algorithm="ES256",
                      headers={"typ": "dpop+jwt", "jwk": public})


def encode_header(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def decode_header(value):
    return json.loads(base64.b64decode(value))


@pytest.fixture
def service(monkeypatch):
    svc = FakePaymentService()
    monkeypatch.setattr(AgenticCommerceVerifier, "_fetch_jwks",
                        lambda self, url, headers: svc.jwks)
    return svc


@pytest.fixture
def es256(monkeypatch):
    monkeypatch.setenv("KEY_AGILITY_ES256_ENABLED", "true")
    monkeypatch.setenv("MOLTRUST_ES256_PRIVATE_KEY", "11" * 32)


class Upstream:
    def __init__(self):
        self.calls = []

    async def __call__(self, offer, value):
        self.calls.append((offer.op, value))
        return 200, {"address": value, "score": 75}


def _signer():
    pem = es256_keys.get_private_key().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    return LocalQuoteSigner.from_pem(pem, kid=es256_keys.ES256_KID, service_url=SERVICE_URL)


def make_client(upstream="default", payment_store=None, nonce_store=None, issuer=ISSUER):
    signer = _signer()
    up = Upstream() if upstream == "default" else upstream
    config = aws.Config(PRODUCT, SERVICE_URL, frozenset(offers.DEFAULT_OPS), issuer)
    asgi = aws.build_app(
        config, signer=signer, seller_public_jwk=signer.public_jwk,
        payment_claim=(payment_store or MemoryPaymentStore()).claim,
        dpop_nonce_store=nonce_store or MemoryNonceStore(), upstream=up,
    )
    parent = FastAPI()
    parent.mount(aws.MOUNT, asgi)

    @parent.get("/health")
    def health():
        return {"ok": True}

    return TestClient(parent), up


@pytest.fixture
def seller(es256, service):
    return make_client()


def _offer(client, op="agent.score", **args):
    args = args or {"address": ADDR}
    r = client.get("/aws/x402", params={"op": op, **args})
    assert r.status_code == 402, r.text
    return decode_header(r.headers["PAYMENT-REQUIRED"])


def _pay(client, accepted, token, op="agent.score", headers=None, **args):
    args = args or {"address": ADDR}
    payload = {"x402Version": 2, "accepted": accepted, "payload": {"paymentToken": token}}
    return client.get("/aws/x402", params={"op": op, **args},
                      headers={"PAYMENT-SIGNATURE": encode_header(payload), **(headers or {})})


# --- the quote --------------------------------------------------------------

def test_unpaid_request_gets_a_signed_es256_quote(seller):
    client, _ = seller
    accepted = _offer(client)["accepts"][0]
    assert (accepted["scheme"], accepted["network"]) == ("exact", "aws:base")
    assert (accepted["amount"], accepted["asset"]) == ("0.01", "iso4217:USD")
    quote = accepted["extra"]["quoteToken"]
    header = json.loads(base64.urlsafe_b64decode(quote.split(".")[0] + "=="))
    assert header["alg"] == "ES256" and header["kid"] == es256_keys.ES256_KID
    claims = jwt.decode(quote, options={"verify_signature": False})
    assert claims["iss"] == SERVICE_URL and claims["aud"] == "aws:marketplace"
    assert claims["settlement"]["product_id"] == PRODUCT
    assert (claims["scheme"], claims["network"]) == ("exact", "aws:base")
    # verifies against our own published EC key
    es256_keys.verify_jws(quote, es256_keys.get_public_jwk())


def test_price_follows_the_operation(seller):
    client, _ = seller
    assert _offer(client, "agent.score")["accepts"][0]["amount"] == "0.01"
    assert _offer(client, "sybil.scan")["accepts"][0]["amount"] == "0.02"


def test_resource_url_is_the_public_endpoint(seller):
    client, _ = seller
    assert _offer(client)["resource"]["url"].startswith(SERVICE_URL)


@pytest.mark.parametrize("params", [
    {}, {"op": "nope", "address": ADDR}, {"op": "agent.score"},
    {"op": "agent.score", "address": "0x1234"},
    {"op": "market.check", "marketId": "abc"},   # not enabled by default
])
def test_bad_requests_get_400_and_no_quote(seller, params):
    client, up = seller
    r = client.get("/aws/x402", params=params)
    assert r.status_code == 400 and "PAYMENT-REQUIRED" not in r.headers
    assert up.calls == []


def test_without_upstream_no_quote_is_issued(es256, service):
    client, _ = make_client(upstream=None)
    r = client.get("/aws/x402", params={"op": "agent.score", "address": ADDR})
    assert r.status_code == 503 and "PAYMENT-REQUIRED" not in r.headers


def test_head_does_not_run_a_paid_route(seller):
    client, up = seller
    r = client.head("/aws/x402", params={"op": "agent.score", "address": ADDR})
    assert r.status_code in (402, 405) and up.calls == []


def test_other_routes_are_untouched(seller):
    client, _ = seller
    assert client.get("/health").status_code == 200


# --- paying ------------------------------------------------------------------

def test_valid_payment_is_served(seller, service):
    client, up = seller
    accepted = _offer(client)["accepts"][0]
    r = _pay(client, accepted, service.issue(accepted["extra"]["quoteToken"]))
    assert r.status_code == 200, r.text
    assert r.json()["result"]["address"] == ADDR
    assert up.calls == [("agent.score", ADDR)]
    settled = decode_header(r.headers["PAYMENT-RESPONSE"])
    assert settled["success"] is True and settled["transaction"].startswith("awsmp-")


def test_same_payment_token_twice_is_refused(seller, service):
    client, up = seller
    accepted = _offer(client)["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"])
    assert _pay(client, accepted, token).status_code == 200
    assert _pay(client, accepted, token).status_code == 402
    assert len(up.calls) == 1


def test_replay_is_refused_across_instances(es256, service):
    shared = MemoryPaymentStore()
    a, _ = make_client(payment_store=shared)
    b, up_b = make_client(payment_store=shared)
    accepted = _offer(a)["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"])
    assert _pay(a, accepted, token).status_code == 200
    assert _pay(b, accepted, token).status_code == 402
    assert up_b.calls == []


def test_payment_store_failure_refuses(es256, service):
    class Broken:
        async def claim(self, *_):
            raise RuntimeError("db down")
    client, up = make_client(payment_store=Broken())
    accepted = _offer(client)["accepts"][0]
    assert _pay(client, accepted, service.issue(accepted["extra"]["quoteToken"])).status_code == 402
    assert up.calls == []


def test_payment_for_a_cheaper_operation_is_refused(seller, service):
    client, _ = seller
    cheap = _offer(client, "agent.score")["accepts"][0]
    token = service.issue(cheap["extra"]["quoteToken"])
    assert _pay(client, cheap, token, op="sybil.scan").status_code == 402


def test_relabelled_price_is_refused(seller, service):
    client, _ = seller
    cheap = _offer(client, "agent.score")["accepts"][0]
    token = service.issue(cheap["extra"]["quoteToken"])
    assert _pay(client, {**cheap, "amount": "0.02"}, token, op="sybil.scan").status_code == 402


def test_token_not_from_the_payment_service_is_refused(seller, service):
    client, _ = seller
    accepted = _offer(client)["accepts"][0]
    forged = FakePaymentService().issue(accepted["extra"]["quoteToken"])
    assert _pay(client, accepted, forged).status_code == 402


@pytest.mark.parametrize("issuer", ["someone-else", "awsmp-agentpay.amazonaws.com"])
def test_wrong_issuer_is_refused(seller, service, issuer):
    client, _ = seller
    accepted = _offer(client)["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"], issuer=issuer)
    assert _pay(client, accepted, token).status_code == 402


def test_issuer_is_configurable(es256, service):
    client, _ = make_client(issuer="awsmp-agentpay.amazonaws.com")
    accepted = _offer(client)["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"], issuer="awsmp-agentpay.amazonaws.com")
    assert _pay(client, accepted, token).status_code == 200


def test_expired_token_is_refused(seller, service):
    client, _ = seller
    accepted = _offer(client)["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"], expires_in=-1)
    assert _pay(client, accepted, token).status_code == 402


def test_token_for_another_quote_is_refused(seller, service):
    client, _ = seller
    accepted = _offer(client)["accepts"][0]
    other = _offer(client)["accepts"][0]
    assert _pay(client, accepted, service.issue(other["extra"]["quoteToken"])).status_code == 402


def test_own_quote_is_not_a_payment(seller, service):
    """AWS: 'your endpoint could accept one of its own quotes as proof of payment'."""
    client, _ = seller
    accepted = _offer(client)["accepts"][0]
    assert _pay(client, accepted, accepted["extra"]["quoteToken"]).status_code == 402


# --- DPoP ----------------------------------------------------------------------

def test_bound_token_with_valid_proof_is_served(seller, service):
    client, _ = seller
    key, jwk = ec_key("buyer")
    offer = _offer(client)
    accepted = offer["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"], dpop_jwk=jwk)
    proof = dpop_proof(key, jwk, htm="GET", htu=SERVICE_URL, access_token=token)
    assert _pay(client, accepted, token, headers={"DPoP": proof}).status_code == 200


def test_bound_token_without_proof_is_refused(seller, service):
    client, _ = seller
    accepted = _offer(client)["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"], dpop_jwk=ec_key("buyer")[1])
    assert _pay(client, accepted, token).status_code == 402


def test_proof_for_another_url_is_refused(seller, service):
    client, _ = seller
    key, jwk = ec_key("buyer")
    accepted = _offer(client)["accepts"][0]
    token = service.issue(accepted["extra"]["quoteToken"], dpop_jwk=jwk)
    proof = dpop_proof(key, jwk, htm="GET", htu="http://127.0.0.1:8000/aws/x402",
                       access_token=token)
    assert _pay(client, accepted, token, headers={"DPoP": proof}).status_code == 402


def test_replayed_dpop_proof_is_refused(es256, service):
    nonces = MemoryNonceStore()
    client, _ = make_client(nonce_store=nonces)
    key, jwk = ec_key("buyer")
    first = _offer(client)["accepts"][0]
    t1 = service.issue(first["extra"]["quoteToken"], dpop_jwk=jwk)
    proof = dpop_proof(key, jwk, htm="GET", htu=SERVICE_URL, access_token=t1)
    assert _pay(client, first, t1, headers={"DPoP": proof}).status_code == 200
    # a second payment presenting the same proof id
    second = _offer(client)["accepts"][0]
    t2 = service.issue(second["extra"]["quoteToken"], dpop_jwk=jwk)
    claims = jwt.decode(proof, options={"verify_signature": False})
    reused = jwt.encode({**claims, "ath": base64.urlsafe_b64encode(
        hashlib.sha256(t2.encode()).digest()).rstrip(b"=").decode()},
        key, algorithm="ES256",
        headers={"typ": "dpop+jwt", "jwk": {k: jwk[k] for k in ("kty", "crv", "x", "y")}})
    assert _pay(client, second, t2, headers={"DPoP": reused}).status_code == 402


# --- mounting ----------------------------------------------------------------------

def test_flag_off_mounts_nothing(monkeypatch):
    monkeypatch.delenv("AWS_X402_ENABLED", raising=False)
    app = FastAPI()
    assert aws.mount_if_enabled(app, lambda: None, "postgresql://x") is False
    assert TestClient(app).get("/aws/x402").status_code == 404


def test_flag_on_without_config_answers_503(monkeypatch, es256):
    monkeypatch.setenv("AWS_X402_ENABLED", "true")
    monkeypatch.delenv("AWS_X402_PRODUCT_ID", raising=False)
    app = FastAPI()
    assert aws.mount_if_enabled(app, lambda: None, "postgresql://x") is True
    r = TestClient(app).get("/aws/x402", params={"op": "agent.score", "address": ADDR})
    assert r.status_code == 503 and r.json()["error"] == "aws_x402_not_ready"


def test_flag_on_without_es256_key_answers_503(monkeypatch):
    monkeypatch.setenv("AWS_X402_ENABLED", "true")
    monkeypatch.setenv("AWS_X402_PRODUCT_ID", PRODUCT)
    monkeypatch.setenv("AWS_X402_SERVICE_URL", SERVICE_URL)
    monkeypatch.delenv("KEY_AGILITY_ES256_ENABLED", raising=False)
    app = FastAPI()
    aws.mount_if_enabled(app, lambda: None, "postgresql://x")
    r = TestClient(app).get("/aws/x402", params={"op": "agent.score", "address": ADDR})
    assert r.status_code == 503 and "Es256Unavailable" in r.json()["reason"]


def test_the_free_listing_id_is_never_the_default():
    assert "k35yywhyj4eqg" not in json.dumps({k: v.__dict__ for k, v in offers.OFFERS.items()},
                                             default=str)
