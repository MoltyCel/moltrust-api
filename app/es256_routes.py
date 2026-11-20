"""Public EC-only key set (KEY-AGILITY, 20.11.2026).

GET /.well-known/jwks-es256.json carries the P-256 registry key and nothing
else. The main /.well-known/jwks.json is a static file in the web root and
stays Ed25519-only until Lars releases the production key (gate).

AWS Marketplace takes a JWKS URL for quote signing; the path must contain
/.well-known/, end in .json, stay under 5 KB and answer within 1.5 s
(docs.aws.amazon.com/marketplace/latest/userguide/agentic-commerce-signing-key.html,
read 20.11.2026). One EC JWK is about 200 bytes.

Flag off: 404, the route does not exist. Flag on, key unusable: 503 with the
reason, never another key in its place.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import es256_keys

router = APIRouter(tags=["Keys"])


@router.get("/.well-known/jwks-es256.json", include_in_schema=False)
async def jwks_es256():
    if not es256_keys.enabled():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    try:
        body = es256_keys.ec_only_jwks()
    except es256_keys.Es256Unavailable as exc:
        return JSONResponse({"error": "es256_key_unavailable", "reason": str(exc)},
                            status_code=503)
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=300"})
