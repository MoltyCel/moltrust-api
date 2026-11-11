"""MolCheck REST routes on the API gateway.

MolCheck is the read-only agent mandate verifier (formerly MoltProof). It runs
as its own service on 127.0.0.1:3006 and answers under /proof/*. This router
serves it as /molcheck/* and keeps /proof/* as a deprecated alias, so the API
speaks one name while every existing integration keeps working.

Both prefixes forward unchanged to the same upstream path: same method, query,
body, status and response headers. Payment (x402) and rate limits stay where
they are, in the MolCheck service. Two things the forward carries explicitly:

  * X-Real-IP is the resolved client address. The service keys its rate limits
    on it when the peer is loopback; without it every caller would share the
    gateway's bucket, and /verdict-free allows one call per ten minutes.
  * X-PAYMENT and the other request headers pass through, so a paid call is
    settled by the service exactly as on /proof/*.

In production nginx sends /proof/ straight to the service and never reaches
these alias handlers; they exist so the alias works wherever the API runs and so
the OpenAPI schema states the deprecation.
"""

from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, Response

MOLCHECK_UPSTREAM = "http://127.0.0.1:3006"
UPSTREAM_PREFIX = "/proof"

# Hop-by-hop headers (RFC 9110 §7.6.1) and the ones httpx sets itself.
_DROP_REQUEST = {"host", "content-length", "connection", "keep-alive", "te",
                 "trailer", "transfer-encoding", "upgrade", "proxy-authorization",
                 "x-real-ip", "x-forwarded-for"}
_DROP_RESPONSE = {"content-length", "content-encoding", "connection", "keep-alive",
                  "transfer-encoding", "trailer", "upgrade"}

# name -> (method, path below the prefix, summary)
ENDPOINTS = {
    "verdict_free": ("GET", "/verdict-free/{agent}",
                     "Free verdict for an agent, rate-limited to one call per ten minutes"),
    "verdict": ("GET", "/verdict/{agent}",
                "Verdict and per-check breakdown for an agent against its committed mandate"),
    "evidence": ("GET", "/evidence/{agent}",
                 "Verdict plus the decoded transactions that breached the mandate"),
    "mandate": ("GET", "/mandate/{agent}", "The committed AAE mandate for an agent"),
    "verify": ("POST", "/verify",
               "Recompute a verdict offline from public inputs and check its signature"),
    "registry": ("GET", "/registry", "Agents with committed mandates and their current verdict"),
    "info": ("GET", "/info", "Service description"),
    "health": ("GET", "/health", "Service health"),
}

router = APIRouter()


def _client_ip(request: Request) -> str:
    from app.main import _get_client_ip
    return _get_client_ip(request)


async def forward(request: Request, upstream_path: str) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST}
    headers["X-Real-IP"] = _client_ip(request)
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.request(request.method, f"{MOLCHECK_UPSTREAM}{upstream_path}",
                                     params=list(request.query_params.multi_items()),
                                     content=body or None, headers=headers)
    except httpx.HTTPError:
        return Response(content=b'{"error":"molcheck_unavailable"}', status_code=502,
                        media_type="application/json")
    out = {k: v for k, v in r.headers.items() if k.lower() not in _DROP_RESPONSE}
    return Response(content=r.content, status_code=r.status_code, headers=out)


def _register(prefix: str, name: str, method: str, path: str, summary: str, deprecated: bool):
    if "{agent}" in path:
        async def handler(request: Request, agent: str) -> Response:
            # Quoted, so an agent value cannot climb out of /proof/ upstream.
            return await forward(request, UPSTREAM_PREFIX + path.replace(
                "{agent}", quote(agent, safe=":@")))
    else:
        async def handler(request: Request) -> Response:
            return await forward(request, UPSTREAM_PREFIX + path)

    handler.__name__ = f"{prefix.strip('/')}_{name}"
    description = (f"Deprecated alias of `/molcheck{path}`, kept so existing integrations "
                   f"do not break. Use `/molcheck{path}`." if deprecated else
                   f"MolCheck: {summary}. Read-only; `/proof{path}` is the deprecated alias.")
    router.add_api_route(prefix + path, handler, methods=[method], summary=summary,
                         description=description, deprecated=deprecated,
                         tags=["MolCheck"], name=handler.__name__)


for _name, (_method, _path, _summary) in ENDPOINTS.items():
    _register("/molcheck", _name, _method, _path, _summary, deprecated=False)
for _name, (_method, _path, _summary) in ENDPOINTS.items():
    _register("/proof", _name, _method, _path, _summary, deprecated=True)
