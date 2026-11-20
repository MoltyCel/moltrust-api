"""AWS Marketplace Agentic Commerce (x402) entry point. Prepared, not active.

AWS-X402 v2, 20.11.2026. Mounted under /aws only when AWS_X402_ENABLED=true;
otherwise nothing of this module is imported by app.main. The USDC x402 routes
are a different code path and stay as they are.

Flow, per the AWS launch post of 19.11.2026 and the seller guide:
1. GET /aws/x402?op=…  without payment → 402, signed quote in PAYMENT-REQUIRED.
   Quote: ES256 with the P-256 key from app.es256_keys, network aws:base,
   asset iso4217:USD, payTo = AWS_X402_PRODUCT_ID.
2. The buyer's agent gets a payment token from AgentCore payments (ES384, signed
   by AWS) and retries with PAYMENT-SIGNATURE (and DPoP if the token is bound).
3. The seller SDK verifies the token offline against the AWS JWKS; our hook
   accepts each payment_id once across all instances; then the request runs.

Built on the AWS seller SDK fiat-extension-for-x402 (Apache-2.0) and x402 core.

Configuration (all in ~/.moltrust_secrets):
  AWS_X402_ENABLED=true
  AWS_X402_PRODUCT_ID=prod-…            the new Agentic Commerce product, not
                                         the free listing prod-k35yywhyj4eqg
  AWS_X402_SERVICE_URL=https://api.moltrust.ch/aws/x402
                                         must equal the EndpointUrl registered
                                         on the listing; becomes the quote iss
  AWS_X402_OPS=agent.score,…            optional, default offers.DEFAULT_OPS
  AWS_X402_PAYMENT_TOKEN_ISSUER=…       optional, see _ISSUER below
  MOLTGUARD_INTERNAL_URL / MOLTGUARD_INTERNAL_TOKEN
                                         upstream for paid operations
  plus KEY_AGILITY_ES256_ENABLED and MOLTRUST_ES256_PRIVATE_KEY.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.aws_x402 import offers

log = logging.getLogger("aws_x402")

FLAG = "AWS_X402_ENABLED"
NETWORK = "aws:base"
MOUNT = "/aws"
ROUTE_PATH = "/x402"

# The AWS seller guide (agentic-commerce-verify-payment-tokens.html, read
# 20.11.2026) requires iss == agentcore-payments.marketplace.amazonaws.com.
# The seller SDK 1.0.2 defaults to awsmp-agentpay.amazonaws.com, and the same
# page says to check whether the SDK verifies iss. The two disagree; the
# primary documentation wins until a real token from the Limited-visibility
# test says otherwise. Override with AWS_X402_PAYMENT_TOKEN_ISSUER.
_ISSUER = "agentcore-payments.marketplace.amazonaws.com"

# Allowance for clock skew when the payment record expires.
_SKEW_SECONDS = 60


class NotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    product_id: str
    service_url: str
    enabled_ops: frozenset[str]
    payment_token_issuer: str
    payment_token_jwks_url: str | None = None   # tests only; None = AWS default

    @staticmethod
    def from_env() -> Config:
        product_id = os.environ.get("AWS_X402_PRODUCT_ID", "").strip()
        service_url = os.environ.get("AWS_X402_SERVICE_URL", "").strip()
        if not product_id.startswith("prod-"):
            raise NotConfigured("AWS_X402_PRODUCT_ID missing or not a prod- id")
        if not service_url.startswith("https://"):
            raise NotConfigured("AWS_X402_SERVICE_URL missing or not https")
        ops_env = os.environ.get("AWS_X402_OPS", "").strip()
        ops = tuple(o.strip() for o in ops_env.split(",") if o.strip()) or offers.DEFAULT_OPS
        unknown = [o for o in ops if o not in offers.OFFERS]
        if unknown:
            raise NotConfigured(f"AWS_X402_OPS names unknown operations: {unknown}")
        issuer = os.environ.get("AWS_X402_PAYMENT_TOKEN_ISSUER", "").strip() or _ISSUER
        return Config(product_id, service_url, frozenset(ops), issuer)


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() == "true"


# --------------------------------------------------------------------------
# Upstream: the operation itself
# --------------------------------------------------------------------------

Upstream = Callable[[offers.Offer, str], Awaitable[tuple[int, Any]]]


def moltguard_upstream() -> Upstream | None:
    """Calls moltguard's route for the operation, marked as already paid.

    moltguard does not honour MOLTGUARD_INTERNAL_TOKEN yet; that is its own PR.
    Without both variables this returns None and the entry point answers 503
    before any 402 is issued, so nobody pays for a request that cannot run.
    """
    base = os.environ.get("MOLTGUARD_INTERNAL_URL", "").strip().rstrip("/")
    token = os.environ.get("MOLTGUARD_INTERNAL_TOKEN", "").strip()
    if not base or not token:
        return None

    async def call(offer: offers.Offer, value: str) -> tuple[int, Any]:
        import httpx
        url = base + offer.upstream_path.format(value=value)
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"X-MolTrust-Internal-Paid": token,
                                               "X-MolTrust-Paid-Via": "aws-marketplace"})
        try:
            body = r.json()
        except ValueError:
            body = {"error": "upstream_not_json"}
        return r.status_code, body

    return call


# --------------------------------------------------------------------------
# The sub-application
# --------------------------------------------------------------------------

def _claims(token: str) -> dict:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def accept_each_payment_once(claim: Callable[[str, int], Awaitable[bool]]):
    """after_verify hook: record the verified payment_id, refuse one already
    recorded. Skips anything that is not a valid aws:base payment, so a forged
    token can never occupy a real payment's id. A store error refuses."""
    from x402.schemas import AbortResult

    async def hook(context):
        if context.requirements.network != NETWORK or not context.result.is_valid:
            return None
        try:
            token = _claims(context.payment_payload.payload["paymentToken"])
            quote = _claims(token["quote_token"])
            expires_at = max(int(token["exp"]), int(quote["exp"])) + _SKEW_SECONDS
            payment_id = str(token["payment_id"])
        except (KeyError, ValueError, TypeError, IndexError):
            return AbortResult(reason="invalid_payment", message="Payment token unreadable")
        try:
            fresh = await claim(payment_id, expires_at)
        except Exception as exc:  # noqa: BLE001 - any store failure must refuse
            log.warning("aws_x402 payment store failed (%s); payment refused", type(exc).__name__)
            return AbortResult(reason="payment_store_unavailable", message="Try again")
        if not fresh:
            return AbortResult(reason="payment_replayed", message="Payment already used")
        return None

    return hook


def _public_url(service_url: str):
    """ASGI wrapper: the URL x402 and DPoP see is the public one buyers call.

    DPoP proofs bind htu to the URL; behind nginx the app would see
    http://127.0.0.1:…. The scheme and host are fixed from AWS_X402_SERVICE_URL
    instead of trusting forwarded headers."""
    parts = urlsplit(service_url)
    host = parts.hostname or ""
    port = parts.port or 443

    def wrap(asgi_app):
        async def wrapped(scope, receive, send):
            if scope.get("type") == "http":
                headers = [(k, v) for k, v in scope.get("headers", []) if k != b"host"]
                headers.append((b"host", host.encode("ascii")))
                scope = {**scope, "scheme": "https", "server": (host, port), "headers": headers}
            await asgi_app(scope, receive, send)
        return wrapped

    return wrap


def build_app(
    config: Config,
    *,
    signer: Any,
    seller_public_jwk: dict,
    payment_claim: Callable[[str, int], Awaitable[bool]],
    dpop_nonce_store: Any,
    upstream: Upstream | None,
) -> Any:
    """The mounted ASGI app. Everything is injected so tests run offline."""
    from fiat_extension_x402 import (
        AgenticCommerceVerifier,
        StaticSellerKeyResolver,
        register_exact_agentic_commerce_facilitator,
        register_exact_agentic_commerce_server,
    )
    from x402 import x402Facilitator, x402ResourceServer
    from x402.http.middleware.fastapi import payment_middleware
    from x402.http.types import PaymentOption, RouteConfig

    verifier = AgenticCommerceVerifier(
        seller_key_resolver=StaticSellerKeyResolver(seller_public_jwk),
        payment_token_jwks_url=config.payment_token_jwks_url,
        payment_token_issuer=config.payment_token_issuer,
    )
    facilitator = register_exact_agentic_commerce_facilitator(x402Facilitator(), verifier=verifier)
    server = register_exact_agentic_commerce_server(
        x402ResourceServer(facilitator), signer=signer, dpop_nonce_store=dpop_nonce_store,
    )
    server.on_after_verify(accept_each_payment_once(payment_claim))

    def price_for(ctx) -> str:
        # Runs only after the guard below accepted op and argument.
        op = ctx.adapter.get_query_param("op")
        return "$" + offers.OFFERS[op].price_usd

    routes = {
        f"GET {MOUNT}{ROUTE_PATH}": RouteConfig(
            accepts=PaymentOption(
                scheme="exact", network=NETWORK, pay_to=config.product_id, price=price_for,
            ),
            description="MolTrust agent trust checks, priced per operation",
        ),
    }

    sub = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @sub.get(ROUTE_PATH)
    async def entry(request: Request):
        offer, value = offers.resolve(request.query_params.get("op"),
                                      dict(request.query_params), config.enabled_ops)
        status, body = await upstream(offer, value)  # type: ignore[misc]
        return JSONResponse({"op": offer.op, "result": body}, status_code=status)

    # Added first, runs second: x402 only sees requests the guard let through.
    sub.middleware("http")(payment_middleware(routes, server))

    @sub.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.rstrip("/").endswith(ROUTE_PATH):
            if request.method != "GET":
                return JSONResponse({"error": "method_not_allowed"}, status_code=405)
            if upstream is None:
                return JSONResponse({"error": "upstream_not_configured"}, status_code=503)
            try:
                offers.resolve(request.query_params.get("op"),
                               dict(request.query_params), config.enabled_ops)
            except offers.OfferError as exc:
                return JSONResponse({"error": "bad_request", "reason": str(exc),
                                     "ops": sorted(config.enabled_ops)}, status_code=400)
        return await call_next(request)

    return _public_url(config.service_url)(sub)


def mount_if_enabled(main_app: FastAPI, get_pool: Callable[[], Any], dsn: str) -> bool:
    """Mount /aws when AWS_X402_ENABLED=true and everything it needs is there.

    Flag on with something missing: mounts a stub that answers 503 with the
    reason, so the endpoint never issues a quote it cannot honour and never
    falls back to another key or an unshared replay store."""
    if not enabled():
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        from fiat_extension_x402 import LocalQuoteSigner

        from app import es256_keys
        from app.aws_x402.stores import PgDPoPNonceStore, PgPaymentStore

        config = Config.from_env()
        pem = es256_keys.get_private_key().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        signer = LocalQuoteSigner.from_pem(pem, kid=es256_keys.ES256_KID,
                                           service_url=config.service_url)
        asgi = build_app(
            config, signer=signer, seller_public_jwk=signer.public_jwk,
            payment_claim=PgPaymentStore(get_pool).claim,
            dpop_nonce_store=PgDPoPNonceStore(dsn),
            upstream=moltguard_upstream(),
        )
    except (NotConfigured, ImportError, RuntimeError, ValueError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
        log.warning("aws_x402 enabled but not ready: %s", reason)
        stub = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @stub.api_route("/{rest:path}", methods=["GET", "POST", "HEAD"])
        async def not_ready(rest: str):
            return JSONResponse({"error": "aws_x402_not_ready", "reason": reason},
                                status_code=503)

        asgi = stub
    main_app.mount(MOUNT, asgi)
    return True
