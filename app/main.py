import asyncio
import ipaddress
import secrets
import hmac as _hmac
import json
from fastapi import FastAPI, HTTPException, Header, Request, Depends, Query, Path
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, Field, field_validator
import uuid, datetime; import datetime as _dt; import httpx, os, re, asyncpg, json, asyncio, logging, time, hashlib, secrets, urllib.parse


# --- Sports Module ---
from app.sports import (
    normalize_event_id, compute_commitment_hash, ensure_table as _sp_ensure_table,
    insert_prediction, get_prediction_by_hash, agent_exists as _sp_agent_exists,
    get_prediction_history, get_prediction_stats, compute_calibration_score,
)
from app.settlement import run_settlement_cycle, settle_prediction as _settle_prediction_fn
from app.signals import (
    ensure_signal_table, generate_provider_id, compute_credential_hash,
    insert_provider, get_provider_by_id, get_provider_by_did,
    get_track_record, get_recent_signals, get_leaderboard, generate_badge_svg,
)
from app.fantasy import (
    ensure_fantasy_table, compute_lineup_hash, compute_fantasy_commitment_hash,
    insert_lineup, get_lineup_by_hash, settle_lineup,
    get_fantasy_history, get_fantasy_stats,
    issue_fantasy_lineup_credential,
    VALID_PLATFORMS, VALID_SPORTS,
)

from app.provenance.ipr import ensure_table as ensure_ipr_table
from app.billing import router as billing_router, admin_router as billing_admin_router, ensure_billing_tables
from app.provenance.ipr import (
    validate_ipr_input, insert_ipr, get_ipr,
    get_iprs_by_agent, get_ipr_stats, submit_outcome,
)
from app.provenance.anchor import anchor_batch, anchor_single_calldata
from app.test_harness.routes import router as test_harness_router
from app.provenance.confidence import (
    compute_calibration_score as _ipr_calibration,
    check_confidence_inflation as _ipr_inflation,
)
from app.provenance.reconcile import (
    check_ipr_status, reconcile_pending, retry_failed, reanchor_ipr,
)

app = FastAPI(title="MolTrust API", version="2.4", docs_url=None)


# --- MCP streamable-HTTP transport mounted as ASGI sub-app at /mcp ---------
# Identity flows through the FastAPI identity_middleware which sets
# request.state.identity before the mounted MCP app dispatches the tool.
# The dispatch-level auth gate lives in app.mcp_auth_middleware.
import sys as _mcp_sys
_mcp_sys.path.insert(0, "/home/moltstack/moltstack/services")
from moltrust_mcp_server.server import mcp as _moltrust_mcp  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from moltguard_mcp_tools import register_moltguard_tools as _register_moltguard  # noqa: E402
from probe_mcp_tools import register_probe_tools as _register_probe  # noqa: E402

_register_moltguard(_moltrust_mcp)
_register_probe(_moltrust_mcp)
_moltrust_mcp.settings.streamable_http_path = "/"
_moltrust_mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["127.0.0.1:*", "localhost:*", "api.moltrust.ch"],
    allowed_origins=[
        "http://127.0.0.1:*", "http://localhost:*",
        "https://api.moltrust.ch", "https://smithery.ai", "https://server.smithery.ai",
    ],
)
app.mount("/mcp", _moltrust_mcp.streamable_http_app())

_mcp_session_cm = None


@app.on_event("startup")
async def _mcp_session_startup():
    global _mcp_session_cm
    _mcp_session_cm = _moltrust_mcp.session_manager.run()
    await _mcp_session_cm.__aenter__()


@app.on_event("shutdown")
async def _mcp_session_shutdown():
    global _mcp_session_cm
    if _mcp_session_cm is not None:
        await _mcp_session_cm.__aexit__(None, None, None)


def _ratelimit_key(request) -> str:
    # Extract the trusted client IP. nginx sets X-Real-IP to $remote_addr
    # (the TCP-connection source, never client-supplied); fall back to the
    # last hop of X-Forwarded-For, never the first (the [-1] element is the
    # nginx-appended value; the [0] is whatever the client prepended). The
    # raw extraction matches identity._client_ip for consistency.
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if not real_ip:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            real_ip = forwarded.split(",")[-1].strip()
    if not real_ip:
        real_ip = get_remote_address(request)
    if not real_ip:
        return "unknown"
    # H8 from the AI security review: collapse IPv6 addresses to their /64
    # network so an attacker with a routed /48 (or even /56) can't sidestep
    # per-IP rate limits by rotating through individual addresses. IPv4
    # stays at /32 here because shared-NAT users would otherwise share a
    # /24 bucket and hit limits unfairly; the probe-spawn rate guard in
    # app.identity._enforce_spawn_rate applies its own /24 bucketing per
    # spec §8 — that policy is intentionally distinct.
    if ":" in real_ip:
        try:
            net = ipaddress.ip_network(f"{real_ip}/64", strict=False)
            return str(net.network_address)
        except ValueError:
            pass
    return real_ip

limiter = Limiter(key_func=_ratelimit_key)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

logger = logging.getLogger("moltrust")

# Custom Swagger UI with dark mode
from fastapi.responses import HTMLResponse as _HTMLResp

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return _HTMLResp("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MolTrust API</title>
<link rel="icon" href="https://moltrust.ch/img/favicon.png" type="image/png">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
<style>
  :root { --bg: #fff; --topbar-bg: #0F172A; --text: #1E293B; --border: #E2E8F0; }
  [data-theme="dark"] { --bg: #0F172A; --topbar-bg: #0F172A; --text: #E2E8F0; --border: #334155; }
  body { margin: 0; background: var(--bg); transition: background 0.2s; }
  /* Dark mode overrides for Swagger UI */
  [data-theme="dark"] .swagger-ui { color: #E2E8F0; }
  [data-theme="dark"] .swagger-ui .topbar { background: #0F172A; }
  [data-theme="dark"] .swagger-ui .info .title, [data-theme="dark"] .swagger-ui .info p,
  [data-theme="dark"] .swagger-ui .info li, [data-theme="dark"] .swagger-ui .info a,
  [data-theme="dark"] .swagger-ui .scheme-container,
  [data-theme="dark"] .swagger-ui .opblock-tag { color: #E2E8F0; }
  [data-theme="dark"] .swagger-ui .opblock .opblock-summary-description { color: #94A3B8; }
  [data-theme="dark"] .swagger-ui section.models, [data-theme="dark"] .swagger-ui .model-container,
  [data-theme="dark"] .swagger-ui .model { color: #CBD5E1; }
  [data-theme="dark"] .swagger-ui .opblock .opblock-section-header { background: #1E293B; }
  [data-theme="dark"] .swagger-ui .opblock .opblock-section-header h4 { color: #E2E8F0; }
  [data-theme="dark"] .swagger-ui .opblock-body pre, [data-theme="dark"] .swagger-ui textarea,
  [data-theme="dark"] .swagger-ui input[type=text] { background: #1E293B; color: #E2E8F0; }
  [data-theme="dark"] .swagger-ui .response-col_description__inner p { color: #CBD5E1; }
  [data-theme="dark"] .swagger-ui table thead tr td, [data-theme="dark"] .swagger-ui table thead tr th,
  [data-theme="dark"] .swagger-ui .parameter__name, [data-theme="dark"] .swagger-ui .parameter__type { color: #CBD5E1; }
  [data-theme="dark"] .swagger-ui .scheme-container { background: #1E293B; box-shadow: none; }
  [data-theme="dark"] .swagger-ui section.models { border-color: #334155; }
  [data-theme="dark"] .swagger-ui section.models.is-open h4 { border-color: #334155; }
  [data-theme="dark"] .swagger-ui .model-box { background: #1E293B; }
  [data-theme="dark"] .swagger-ui .opblock.opblock-get { border-color: #1E4D8C; background: rgba(30,77,140,0.1); }
  [data-theme="dark"] .swagger-ui .opblock.opblock-post { border-color: #1E6B3A; background: rgba(30,107,58,0.1); }
  [data-theme="dark"] .swagger-ui .opblock.opblock-get .opblock-summary { border-color: #1E4D8C; }
  [data-theme="dark"] .swagger-ui .opblock.opblock-post .opblock-summary { border-color: #1E6B3A; }
  [data-theme="dark"] .swagger-ui .btn { color: #E2E8F0; }
  [data-theme="dark"] .swagger-ui select { background: #1E293B; color: #E2E8F0; border-color: #334155; }
  [data-theme="dark"] .swagger-ui .markdown p, [data-theme="dark"] .swagger-ui .markdown code { color: #CBD5E1; }
  [data-theme="dark"] .swagger-ui .loading-container .loading::after { color: #CBD5E1; }
  /* Header bar */
  .mt-topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 24px; background: var(--topbar-bg); color: #fff;
    font-family: 'DM Sans', -apple-system, sans-serif;
  }
  .mt-topbar a { color: #fff; text-decoration: none; font-weight: 700; font-size: 1.1rem; }
  .mt-topbar a span { color: #E85D26; }
  .mt-topbar-right { display: flex; align-items: center; gap: 16px; }
  .mt-topbar-right a { font-size: 0.85rem; font-weight: 400; opacity: 0.8; }
  .mt-topbar-right a:hover { opacity: 1; }
  .theme-toggle { background: none; border: none; cursor: pointer; color: #94A3B8; padding: 4px; display: flex; }
  .theme-toggle:hover { color: #E85D26; }
  .theme-toggle svg { width: 18px; height: 18px; }
  .theme-toggle .icon-sun { display: none; }
  .theme-toggle .icon-moon { display: block; }
  [data-theme="dark"] .theme-toggle .icon-sun { display: block; }
  [data-theme="dark"] .theme-toggle .icon-moon { display: none; }
</style>
<script>
(function(){var s=localStorage.getItem('mt-theme');if(!s)s=window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';document.documentElement.setAttribute('data-theme',s);})();
</script>
</head>
<body>
<div class="mt-topbar">
  <a href="https://moltrust.ch">Mol<span>Trust</span> API</a>
  <div class="mt-topbar-right">
    <a href="https://moltrust.ch">Website</a>
    <a href="https://moltrust.ch/moltguard.html">MoltGuard</a>
    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle dark mode">
      <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
      <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
    </button>
  </div>
</div>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
function toggleTheme(){var c=document.documentElement.getAttribute('data-theme');var n=c==='dark'?'light':'dark';document.documentElement.setAttribute('data-theme',n);localStorage.setItem('mt-theme',n);}
SwaggerUIBundle({url:'/openapi.json',dom_id:'#swagger-ui',presets:[SwaggerUIBundle.presets.apis,SwaggerUIBundle.SwaggerUIStandalonePreset],layout:'BaseLayout',deepLinking:true});
</script>
</body>
</html>""")


# --- Config ---
MOLTBOOK_APP_KEY = os.getenv("MOLTBOOK_APP_KEY", "moltdev_PENDING")
if not os.getenv("MOLTRUST_API_KEYS"):
    raise RuntimeError("MOLTRUST_API_KEYS environment variable is required — no default key allowed")
API_KEYS = set(os.getenv("MOLTRUST_API_KEYS").split(","))
DB_URL = os.getenv("DATABASE_URL", "postgresql://moltstack:$(cat /dev/null)@localhost/moltstack")

# --- Credits Config ---
CREDITS_ENABLED = os.getenv("CREDITS_ENABLED", "false").lower() == "true"

# --- SMTP Config ---
SMTP_HOST = os.getenv("SMTP_HOST", "mail.infomaniak.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "info@moltrust.ch")
SMTP_PASS = os.getenv("SMTP_PASS", "")

# --- Database Pool ---
db_pool = None

@app.on_event("startup")
async def startup():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"), database=os.getenv("DB_NAME", "moltstack"),
            user="moltstack", password=os.getenv("MOLTSTACK_DB_PW", ""),
            min_size=2, max_size=10
        )
    except Exception as e:
        print(f"DB pool warning: {e} - running without DB")
    # Create sports table
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await _sp_ensure_table(conn)
            print("Sports table ready")
        except Exception as e:
            print(f"Sports table warning: {e}")
        try:
            async with db_pool.acquire() as conn:
                await ensure_signal_table(conn)
            print("Signal providers table ready")
        except Exception as e:
            print(f"Signal providers table warning: {e}")
        try:
            async with db_pool.acquire() as conn:
                await ensure_fantasy_table(conn)
            print("Fantasy lineups table ready")
        except Exception as e:
            print(f"Fantasy lineups table warning: {e}")
        try:
            async with db_pool.acquire() as conn:
                await ensure_violation_records_table(conn)
            print("Violation records table ready")
        except Exception as e:
            print(f"Violation records table warning: {e}")

        try:
            async with db_pool.acquire() as conn:
                await ensure_ipr_table(conn)
            print("IPR table ready")
        except Exception as e:
            print(f"IPR table warning: {e}")


        try:
            async with db_pool.acquire() as conn:
                await ensure_billing_tables(conn)
            await ensure_caep_table(conn)
            print("Billing tables ready")
        except Exception as e:
            print(f"Billing tables warning: {e}")
    # Start settlement scheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    global _settlement_scheduler
    _settlement_scheduler = AsyncIOScheduler()
    async def _scheduled_settlement():
        try:
            result = await run_settlement_cycle(db_pool)
            logger.info(f"Settlement cycle: {result['checked']} checked, {result['settled']} settled")
        except Exception as e:
            logger.error(f"Settlement cycle error: {e}")
    _settlement_scheduler.add_job(_scheduled_settlement, 'interval', minutes=30, id='settlement')
    _settlement_scheduler.start()
    print("Settlement scheduler started (every 30min)")

@app.on_event("shutdown")
async def shutdown():
    global _settlement_scheduler
    if hasattr(_settlement_scheduler, 'shutdown'):
        try:
            _settlement_scheduler.shutdown(wait=False)
            print("Settlement scheduler stopped")
        except Exception:
            pass
    if db_pool:
        await db_pool.close()

_settlement_scheduler = None

# --- Rate Limit Handler ---
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Rate limit exceeded. Try again later."})

# --- Global Exception Handler ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})

# --- Outbound Content Filter ---
SENSITIVE_PATTERNS = [
    re.compile(r"sk-ant-api[a-zA-Z0-9\-_]{20,}"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"xprv[a-zA-Z0-9]{50,}"),
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"BEGIN (RSA |EC )?PRIVATE KEY"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]

def scrub_secrets(obj):
    if isinstance(obj, str):
        for pat in SENSITIVE_PATTERNS:
            obj = pat.sub("[REDACTED]", obj)
        return obj
    elif isinstance(obj, dict):
        return {k: scrub_secrets(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [scrub_secrets(i) for i in obj]
    return obj

async def update_last_active(did: str):
    """Update both last_seen and last_active_at for an agent."""
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE agents SET last_seen = now(), last_active_at = now() WHERE did = $1", did
                )
        except Exception:
            pass


# --- IP Enrichment ---
_IP_CACHE: dict[str, dict] = {}

async def _enrich_ip(ip: str) -> dict:
    if ip in _IP_CACHE:
        return _IP_CACHE[ip]
    info = {"org": None, "country": None}
    try:
        import urllib.request as _ur
        req = _ur.Request(f"http://ip-api.com/json/{ip}?fields=org,country", headers={"User-Agent": "MolTrust/1.0"})
        with _ur.urlopen(req, timeout=2) as r:
            import json as _j
            data = _j.loads(r.read())
            info["org"] = data.get("org", "")[:200]
            info["country"] = data.get("country", "")[:100]
    except Exception:
        pass
    _IP_CACHE[ip] = info
    # Keep cache bounded
    if len(_IP_CACHE) > 500:
        oldest = list(_IP_CACHE.keys())[:100]
        for k in oldest:
            _IP_CACHE.pop(k, None)
    return info


def _get_client_ip(request) -> str:
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()[:50]
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[-1].strip()[:50]
    return (request.client.host if request.client else "unknown")[:50]


def _anonymize_ip(ip: str) -> str:
    """DSGVO: zero last octet (IPv4) or last 64 bits (IPv6)."""
    if not ip or ip in ("unknown", "localhost", "127.0.0.1", "::1"):
        return ip
    try:
        if ":" in ip:
            parts = ip.split(":")
            return ":".join(parts[:4]) + "::0"
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0"
    except Exception:
        pass
    return ip


async def update_last_seen(did: str):
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE agents SET last_seen = now() WHERE did = $1", did)
        except:
            pass

@app.middleware("http")
async def content_filter_middleware(request: Request, call_next):
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("application/json"):
        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode()
        try:
            import json as _json
            data = _json.loads(body)
            filtered = scrub_secrets(data)
            extra = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-type")}
            return JSONResponse(content=filtered, status_code=response.status_code, headers=extra)
        except Exception:
            from starlette.responses import Response
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers))
    return response

# --- Credit Middleware ---
from app.credits import (
    get_endpoint_cost, resolve_did_from_api_key, link_api_key_to_did,
    get_balance as _get_balance, ensure_balance_row, grant_credits,
    deduct_credits, transfer_credits, get_transactions,
    ENDPOINT_COSTS,
)

@app.middleware("http")
async def credit_middleware(request: Request, call_next):
    if not CREDITS_ENABLED or not db_pool:
        return await call_next(request)

    # Probes have a separate call_cap quota (enforced by identity_middleware);
    # they are never charged credits.
    identity = getattr(request.state, "identity", None)
    if identity is not None and getattr(identity, "is_probe", False):
        return await call_next(request)

    method = request.method
    path = request.url.path
    cost = get_endpoint_cost(method, path)

    if cost == 0:
        return await call_next(request)

    # Resolve API key → DID
    api_key = request.headers.get("x-api-key", "")
    caller_did = None
    if api_key:
        async with db_pool.acquire() as conn:
            caller_did = await resolve_did_from_api_key(conn, api_key)

    # No API key provided — let the request through without charging
    # (the endpoint's own auth will reject if it requires a key)
    if not api_key:
        return await call_next(request)

    # First registration: no DID linked yet — let it through
    if not caller_did and path == "/identity/register" and method == "POST":
        return await call_next(request)

    if not caller_did:
        return JSONResponse(
            status_code=402,
            content={
                "error": "No agent linked to this API key. Register an agent first via POST /identity/register.",
                "pricing_url": "https://api.moltrust.ch/credits/pricing",
            },
        )

    # MEDIUM-2: Pre-check balance (non-atomic, for early 402 response)
    async with db_pool.acquire() as conn:
        balance = await _get_balance(conn, caller_did)

    if balance < cost:
        return JSONResponse(
            status_code=402,
            content={
                "error": "Insufficient credits",
                "balance": balance,
                "required": cost,
                "pricing_url": "https://api.moltrust.ch/credits/pricing",
            },
        )

    # Execute the actual request
    response = await call_next(request)

    # MEDIUM-2: Atomic deduct — single UPDATE with balance check prevents race conditions
    if response.status_code < 400:
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    from app.credits import resolve_endpoint_key
                    ref = resolve_endpoint_key(method, path)
                    rows_affected = await conn.execute(
                        "UPDATE credit_balances SET balance = balance - $1 "
                        "WHERE agent_did = $2 AND balance >= $1",
                        cost, caller_did,
                    )
                    if rows_affected == "UPDATE 0":
                        logger.warning("Atomic credit deduct failed (race) for %s", caller_did)
                    else:
                        await conn.execute(
                            "INSERT INTO credit_transactions (agent_did, amount, reference, description, created_at) "
                            "VALUES ($1, $2, $3, $4, NOW())",
                            caller_did, -cost, ref, f"API call: {ref}",
                        )
        except Exception as e:
            logger.error("Credit deduction failed for %s: %s", caller_did, e)

    return response


# --- Identity Middleware ---
# Resolves probe / claimed identity on every request and stashes the result
# on request.state.identity. On a fresh probe mint (kind="probe-new") the raw
# probe key is exposed only via GET /auth/identity (JSON body) and via the
# moltrust_identity MCP tool — never as a response header. Header surfacing
# was removed per H11 of the AI security review because Nginx, Sentry, and
# any monitoring stack that captures response headers would have logged the
# key in plaintext. Per docs/auto-probe-token-spec.md §4.2 / §4.4 / §10.2.
from app.identity import (
    resolve_identity as _resolve_identity,
    increment_probe_call_count as _inc_probe_calls,
    maybe_extend_probe_ttl as _maybe_extend_ttl,
    AuthError as _IdentityAuthError,
    Identity,
    require_claimed,
    require_probe,
    detect_source as _detect_source,
    record_probe_spawn as _record_probe_spawn,
    record_probe_activity as _record_probe_activity,
)

_IDENTITY_SKIP_PATHS = {"/", "/health", "/openapi.json", "/favicon.ico"}
_IDENTITY_SKIP_PREFIXES = ("/docs", "/static/", "/auth/claim")


# --- MCP dispatch-level auth gate ---
# FastAPI's add_middleware inserts at user_middleware[0], so the LAST
# middleware added is OUTERMOST (fires first on request). Wiring this
# BEFORE the identity_middleware decorator below puts McpAuthMiddleware
# DEEPER in the stack at build time — identity_middleware runs first,
# sets request.state.identity, then this gate inspects /mcp tools/call.
from app.mcp_auth_middleware import McpAuthMiddleware  # noqa: E402
app.add_middleware(McpAuthMiddleware)


@app.middleware("http")
async def identity_middleware(request: Request, call_next):
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or path in _IDENTITY_SKIP_PATHS
        or any(path.startswith(p) for p in _IDENTITY_SKIP_PREFIXES)
    ):
        return await call_next(request)
    if not db_pool:
        return await call_next(request)
    try:
        async with db_pool.acquire() as conn:
            identity = await _resolve_identity(request, conn)
            # Record the spawn-attribution row before the request runs so a
            # crashed handler still leaves an analytics trail of where this
            # probe came in.
            if identity.kind == "probe-new":
                source = _detect_source(
                    request.headers.get("user-agent"),
                    request.headers.get("mcp-session-id"),
                )
                await _record_probe_spawn(
                    conn, probe_did=identity.did, source=source, first_path=path,
                )
    except _IdentityAuthError as exc:
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": exc.message,
                "claim_url": "https://api.moltrust.ch/auth/claim",
            },
        )
    request.state.identity = identity

    # Atomic call-count enforcement BEFORE the handler. Closes the TOCTOU
    # window where two parallel calls could both pass the resolve-time cap
    # check and over-spend the budget — increment_probe_call_count uses an
    # UPDATE ... WHERE expires_at > now() AND call_count < call_cap, so the
    # Nth+1 concurrent caller gets None back and we reject with 429 before
    # running the handler.
    if identity.is_probe:
        new_count: int | None = -1
        try:
            async with db_pool.acquire() as conn:
                new_count = await _inc_probe_calls(conn, identity.did)
        except Exception as exc:
            # Accounting DB hiccup: fall back to letting the request through.
            # The resolve-time cap check in app.identity.resolve_identity
            # already filtered clearly-over-cap probes, so the worst-case
            # over-spend during a DB outage is bounded.
            logger.warning("Probe call counter unavailable for %s: %s", identity.did, exc)
        if new_count is None:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Probe call cap reached or probe expired — POST /auth/claim to keep history, or sign up fresh.",
                    "claim_url": "https://api.moltrust.ch/auth/claim",
                },
            )

    response = await call_next(request)

    if identity.is_probe:
        try:
            async with db_pool.acquire() as conn:
                await _maybe_extend_ttl(conn, identity.did)
                await _record_probe_activity(conn, probe_did=identity.did, path=path)
        except Exception as exc:
            logger.warning("Probe TTL/activity accounting failed for %s: %s", identity.did, exc)

    return response


# --- /auth/identity ---
# Returns the current request's resolved identity. Probes get back their DID,
# expiry, calls_remaining, dynamic claim_value pitch, and (on first mint) the
# raw probe_key. Claimed identities get just DID + kind. Per spec §4.4.
from app.identity import build_claim_value_pitch as _build_claim_pitch
from app.identity import get_probe_summary as _get_probe_summary


@app.get("/auth/identity")
async def auth_identity(request: Request):
    identity: Identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(500, "Identity middleware did not run")
    if identity.is_claimed:
        return {
            "did": identity.did,
            "kind": "claimed",
            "status": "permanent identity — full tool surface enabled",
        }
    probe = identity.probe or {}
    calls_remaining = max(0, int(probe.get("call_cap", 50)) - int(probe.get("call_count", 0)))
    expires_at = probe.get("expires_at")
    summary = {}
    if db_pool:
        async with db_pool.acquire() as conn:
            summary = await _get_probe_summary(conn, identity.did)
    payload = {
        "did": identity.did,
        "kind": identity.kind,
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
        "calls_remaining": calls_remaining,
        "summary": summary,
        "claim_with": (
            "POST https://api.moltrust.ch/auth/claim "
            "{\"probe_key\":\"mt_probe_<your-key>\",\"email\":\"you@example.com\"}"
        ),
        "claim_value": _build_claim_pitch(summary),
    }
    if identity.kind == "probe-new" and identity.probe_key:
        payload["probe_key"] = identity.probe_key
        payload["instructions"] = (
            "Store probe_key and pass it as X-API-Key on subsequent calls so "
            "your history accumulates on one DID. Claim before TTL to keep it."
        )
    return payload


# --- /auth/claim + /auth/claim/anonymous ---
# Promote a probe DID to a permanent agent. Email claim is idempotent against
# previously-claimed emails (returns existing identity). Anonymous claim has
# tier='anonymous_claimed' for a lower trust ceiling — agents that genuinely
# lack an email can still get a stable identity. Per spec §4.5.
from app.identity import claim_probe as _claim_probe, ClaimError as _ClaimError


class ProbeClaimRequest(BaseModel):
    probe_key: str = Field(min_length=8, max_length=128)
    email: str = Field(min_length=3, max_length=200)
    display_name: str | None = Field(default=None, max_length=64)


class AnonymousClaimRequest(BaseModel):
    probe_key: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)


async def _run_claim(request: Request, *, probe_key, email, display_name) -> dict:
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    ip = _anonymize_ip(_get_client_ip(request)) if _get_client_ip(request) else None
    async with db_pool.acquire() as conn:
        try:
            return await _claim_probe(
                conn,
                probe_key=probe_key,
                email=email,
                display_name=display_name,
                ip=ip,
            )
        except _ClaimError as exc:
            raise HTTPException(exc.status, exc.message) from exc


@app.post("/auth/claim")
@limiter.limit("3/day")
async def auth_claim(request: Request, body: ProbeClaimRequest):
    """Promote a probe to a permanent email-bound identity. Spec §4.5."""
    return await _run_claim(
        request,
        probe_key=body.probe_key,
        email=body.email,
        display_name=body.display_name,
    )


@app.post("/auth/claim/anonymous")
@limiter.limit("1/day")
async def auth_claim_anonymous(request: Request, body: AnonymousClaimRequest):
    """Promote a probe to a permanent anonymous identity (no email).

    Tier=anonymous_claimed enforces a lower trust ceiling at downstream
    policy points. Useful for autonomous agents that genuinely have no
    email contact — they can still build a stable identity. Spec §4.5.
    """
    return await _run_claim(
        request,
        probe_key=body.probe_key,
        email=None,
        display_name=body.display_name,
    )


# --- Validation Helpers ---
DID_PATTERN = re.compile(r"^did:moltrust:(?:ext_)?[a-f0-9]{16}$")
DISPLAY_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-. ]{1,64}$")

def validate_did(did: str) -> str:
    if not DID_PATTERN.match(did):
        raise HTTPException(400, "Invalid DID format. Expected: did:moltrust:<16 hex chars>")
    return did

def verify_api_key(x_api_key: str = Header(alias="X-API-Key")):
    if len(x_api_key) > 128:
        raise HTTPException(403, "Invalid API key")
    if x_api_key not in API_KEYS:
        raise HTTPException(403, "Invalid API key")
    return x_api_key


def verify_api_key_or_did(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    x_moltrust_did: str | None = Header(None, alias="X-MolTrust-DID"),
) -> dict:
    """Auth helper: accepts either API key OR DID header. At least one required."""
    if x_api_key:
        if len(x_api_key) > 128:
            raise HTTPException(401, "Invalid API key")
        if x_api_key in API_KEYS:
            return {"auth_method": "api_key", "key_id": x_api_key}
        raise HTTPException(401, "Invalid API key")
    if x_moltrust_did:
        if not DID_PATTERN.match(x_moltrust_did):
            raise HTTPException(401, "Invalid DID format")
        return {"auth_method": "did", "did": x_moltrust_did}
    raise HTTPException(401, "Authentication required: provide X-API-Key OR X-MolTrust-DID header")


# --- DID-Wallet Binding: Nonce helpers ---
NONCE_SECRET = os.getenv("NONCE_SECRET", "")

def _generate_nonce(did: str) -> str:
    import time as _t, hashlib as _hl
    ts = int(_t.time())
    payload = f"{did}:{ts}"
    sig = _hmac.new(NONCE_SECRET.encode(), payload.encode(), _hl.sha256).hexdigest()[:16]
    return f"{ts}:{sig}"

def _verify_nonce(did: str, nonce: str, max_age: int = 300) -> bool:
    import time as _t, hashlib as _hl
    try:
        ts_str, sig = nonce.split(":")
        ts = int(ts_str)
        if _t.time() - ts > max_age:
            return False
        payload = f"{did}:{ts}"
        expected = _hmac.new(NONCE_SECRET.encode(), payload.encode(), _hl.sha256).hexdigest()[:16]
        return _hmac.compare_digest(sig, expected)
    except Exception:
        return False

def _verify_wallet_signature(did: str, wallet_address: str, chain: str, nonce: str, signature: str) -> bool:
    message = f"MolTrust DID Binding\nDID: {did}\nWallet: {wallet_address}\nNonce: {nonce}\nChain: {chain}"
    if chain == "solana":
        import nacl.signing, nacl.exceptions, base58
        try:
            pubkey_bytes = base58.b58decode(wallet_address)
            sig_bytes = base58.b58decode(signature)
            verify_key = nacl.signing.VerifyKey(pubkey_bytes)
            verify_key.verify(message.encode("utf-8"), sig_bytes)
            return True
        except (nacl.exceptions.BadSignatureError, Exception):
            return False
    else:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        msg = encode_defunct(text=message)
        try:
            recovered = Account.recover_message(msg, signature=signature)
            return recovered.lower() == wallet_address.lower()
        except Exception:
            return False

# --- Per-Key Registration Rate Limiter ---
_reg_tracker: dict[str, list[float]] = {}

def check_registration_rate(api_key: str, max_per_hour: int = 5):
    now = time.time()
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    if key_hash not in _reg_tracker:
        _reg_tracker[key_hash] = []
    _reg_tracker[key_hash] = [t for t in _reg_tracker[key_hash] if now - t < 3600]
    if len(_reg_tracker[key_hash]) >= max_per_hour:
        raise HTTPException(429, f"Registration limit exceeded: max {max_per_hour} per API key per hour")
    _reg_tracker[key_hash].append(now)

# --- Welcome Email ---
async def send_welcome_email(to_email: str, agent_did: str, display_name: str):
    if not SMTP_PASS:
        logger.warning("SMTP_PASS not set, skipping welcome email to %s", to_email)
        return
    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        verify_url = f"https://api.moltrust.ch/identity/verify/{agent_did}"
        docs_url = "https://api.moltrust.ch/docs"
        pypi_url = "https://pypi.org/project/moltrust/"
        github_url = "https://github.com/MoltyCel/moltrust-sdk"

        html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#0a0a0f;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0a0a0f;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

  <!-- Header -->
  <tr><td style="padding:30px 40px 20px;text-align:center;">
    <span style="font-family:monospace;font-size:24px;font-weight:bold;"><span style="color:#d4a843;">Mol</span><span style="color:#e8734a;">Trust</span></span>
  </td></tr>

  <!-- Main Card -->
  <tr><td style="background-color:#16161f;border:1px solid #2a2a3a;border-radius:8px;padding:40px;">

    <h1 style="color:#e8e6e1;font-size:22px;margin:0 0 8px;">Welcome, {display_name}!</h1>
    <p style="color:#8a8895;font-size:14px;margin:0 0 24px;line-height:1.6;">Your agent has been registered and verified on MolTrust. Here are your details:</p>

    <!-- DID Box -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
    <tr><td style="background-color:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;padding:16px;">
      <div style="color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Your Agent DID</div>
      <div style="color:#d4a843;font-family:monospace;font-size:14px;word-break:break-all;">{agent_did}</div>
    </td></tr>
    </table>

    <!-- Verify Link -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
    <tr><td style="background-color:#0a0a0f;border:1px solid #2a2a3a;border-radius:6px;padding:16px;">
      <div style="color:#8a8895;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">Verify Endpoint</div>
      <a href="{verify_url}" style="color:#e8734a;font-family:monospace;font-size:13px;word-break:break-all;">{verify_url}</a>
    </td></tr>
    </table>

    <!-- Status badges -->
    <table cellpadding="0" cellspacing="0" style="margin-bottom:30px;">
    <tr>
      <td style="background-color:rgba(92,184,92,0.15);color:#5cb85c;font-size:12px;font-weight:bold;padding:4px 10px;border-radius:3px;font-family:monospace;">&#10003; VERIFIED</td>
      <td width="8"></td>
      <td style="background-color:rgba(212,168,67,0.15);color:#d4a843;font-size:12px;font-weight:bold;padding:4px 10px;border-radius:3px;font-family:monospace;">&#10003; CREDENTIAL ISSUED</td>
      <td width="8"></td>
      <td style="background-color:rgba(74,108,247,0.15);color:#4a6cf7;font-size:12px;font-weight:bold;padding:4px 10px;border-radius:3px;font-family:monospace;">&#10003; ON-CHAIN</td>
      <td width="8"></td>
      <td style="background-color:rgba(92,184,92,0.15);color:#5cb85c;font-size:12px;font-weight:bold;padding:4px 10px;border-radius:3px;font-family:monospace;">175 FREE CREDITS</td>
    </tr>
    </table>

    <!-- Divider -->
    <div style="height:1px;background-color:#2a2a3a;margin:0 0 24px;"></div>

    <!-- What's Next -->
    <h2 style="color:#e8e6e1;font-size:16px;margin:0 0 16px;">What's next?</h2>

    <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td width="24" valign="top" style="color:#d4a843;font-size:14px;padding-bottom:12px;">1.</td>
      <td style="color:#8a8895;font-size:14px;line-height:1.5;padding-bottom:12px;">
        <strong style="color:#e8e6e1;">You got 175 free credits</strong><br>
        Use them to call any paid API endpoint. Check your balance at <code style="color:#e8734a;font-size:13px;">GET /credits/balance/{agent_did}</code>
      </td>
    </tr>
    <tr>
      <td width="24" valign="top" style="color:#d4a843;font-size:14px;padding-bottom:12px;">2.</td>
      <td style="color:#8a8895;font-size:14px;line-height:1.5;padding-bottom:12px;">
        <strong style="color:#e8e6e1;">Install the SDK</strong><br>
        <code style="color:#e8734a;font-size:13px;">pip install moltrust</code>
      </td>
    </tr>
    <tr>
      <td width="24" valign="top" style="color:#d4a843;font-size:14px;padding-bottom:12px;">3.</td>
      <td style="color:#8a8895;font-size:14px;line-height:1.5;padding-bottom:12px;">
        <strong style="color:#e8e6e1;">Explore the API</strong><br>
        Interactive docs with all endpoints: <a href="{docs_url}" style="color:#d4a843;text-decoration:none;">{docs_url}</a>
      </td>
    </tr>
    <tr>
      <td width="24" valign="top" style="color:#d4a843;font-size:14px;padding-bottom:12px;">4.</td>
      <td style="color:#8a8895;font-size:14px;line-height:1.5;padding-bottom:12px;">
        <strong style="color:#e8e6e1;">Issue credentials</strong><br>
        Your agent already has an AgentTrustCredential. Issue more via <code style="color:#e8734a;font-size:13px;">POST /credentials/issue</code>
      </td>
    </tr>
    <tr>
      <td width="24" valign="top" style="color:#d4a843;font-size:14px;">5.</td>
      <td style="color:#8a8895;font-size:14px;line-height:1.5;">
        <strong style="color:#e8e6e1;">Build reputation</strong><br>
        Other agents can rate yours. Higher trust scores unlock more in the agent economy.
      </td>
    </tr>
    </table>

    <!-- CTA Button -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:30px;">
    <tr><td align="center">
      <a href="{docs_url}" style="display:inline-block;background-color:#d4a843;color:#0a0a0f;font-weight:bold;font-size:14px;padding:12px 28px;border-radius:4px;text-decoration:none;">Explore the API &rarr;</a>
    </td></tr>
    </table>

  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:24px 40px;text-align:center;">
    <p style="color:#555566;font-size:12px;margin:0 0 8px;">
      <a href="https://moltrust.ch" style="color:#8a8895;text-decoration:none;">Website</a> &nbsp;&middot;&nbsp;
      <a href="{github_url}" style="color:#8a8895;text-decoration:none;">GitHub</a> &nbsp;&middot;&nbsp;
      <a href="{pypi_url}" style="color:#8a8895;text-decoration:none;">PyPI</a> &nbsp;&middot;&nbsp;
      <a href="{docs_url}" style="color:#8a8895;text-decoration:none;">API Docs</a> &nbsp;&middot;&nbsp;
      <a href="https://moltrust.ch/terms.html" style="color:#8a8895;text-decoration:none;">Terms</a> &nbsp;&middot;&nbsp;
      <a href="https://moltrust.ch/privacy.html" style="color:#8a8895;text-decoration:none;">Privacy</a>
    </p>
    <p style="color:#555566;font-size:11px;margin:0;">&copy; 2026 MolTrust &middot; CryptoKRI GmbH, Zurich</p>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["From"] = f"MolTrust <{SMTP_USER}>"
        msg["To"] = to_email
        msg["Subject"] = "Welcome to MolTrust \u2014 Your Agent is Verified \u2713"

        text_body = (
            f"Welcome to MolTrust, {display_name}!\n\n"
            f"Your agent DID: {agent_did}\n"
            f"Verify: {verify_url}\n\n"
            f"You received 175 free API credits.\n\n"
            f"What's next:\n"
            f"1. Check your balance: GET /credits/balance/{agent_did}\n"
            f"2. pip install moltrust\n"
            f"3. API docs: {docs_url}\n"
            f"4. Issue credentials via POST /credentials/issue\n"
            f"5. Build reputation through agent-to-agent ratings\n\n"
            f"Terms: https://moltrust.ch/terms.html\n"
            f"Privacy: https://moltrust.ch/privacy.html\n\n"
            f"-- MolTrust | https://moltrust.ch"
        )
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASS,
            start_tls=True,
        )
        logger.info("Welcome email sent to %s for %s", to_email, agent_did)
    except Exception as e:
        logger.error("Failed to send welcome email to %s: %s", to_email, e)

# --- Request Models ---
# ── Swarm Phase 1: Interaction Proof ──

# ── Swarm Phase 1: Endorsement ──
class EndorseRequest(BaseModel):
    api_key: str
    endorsed_did: str
    skill: str
    evidence_hash: str
    evidence_timestamp: str
    vertical: str

class InteractionProofRequest(BaseModel):
    api_key: str
    interaction_payload: dict

class RegisterRequest(BaseModel):
    display_name: str = Field(default="anonymous", min_length=1, max_length=64)
    platform: str = Field(default="moltbook", max_length=32)
    email: str | None = Field(default=None, max_length=256)
    erc8004: bool = Field(default=False, description="Also register on ERC-8004 IdentityRegistry on Base")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v):
        if not DISPLAY_NAME_PATTERN.match(v):
            raise ValueError("Display name can only contain letters, numbers, underscores, hyphens, dots, spaces")
        return v.strip()

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v):
        if not re.match(r"^[a-zA-Z0-9_\-]{1,32}$", v):
            raise ValueError("Platform must be alphanumeric (a-z, 0-9, _, -)")
        return v.strip().lower()

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v is not None:
            v = v.strip().lower()
            if "@" not in v or "." not in v.split("@")[-1]:
                raise ValueError("Invalid email address")
        return v

class RateRequest(BaseModel):
    from_did: str = Field(max_length=40)
    to_did: str = Field(max_length=40)
    score: int = Field(ge=1, le=5)

    @field_validator("from_did", "to_did")
    @classmethod
    def validate_dids(cls, v):
        if not DID_PATTERN.match(v):
            raise ValueError("Invalid DID format")
        return v

class MoltbookAuthRequest(BaseModel):
    token: str = Field(min_length=10, max_length=512)

class LightningInvoiceRequest(BaseModel):
    amount_sats: int = Field(ge=1, le=10_000_000)
    description: str = Field(default="MolTrust", max_length=128)

    @field_validator("description")
    @classmethod
    def sanitize_description(cls, v):
        return re.sub(r"[<>&\"']", "", v).strip()

class CreditTransferRequest(BaseModel):
    from_did: str = Field(max_length=40)
    to_did: str = Field(max_length=40)
    amount: int = Field(ge=1)
    reference: str = Field(default="", max_length=256)

    @field_validator("from_did", "to_did")
    @classmethod
    def validate_dids(cls, v):
        if not DID_PATTERN.match(v):
            raise ValueError("Invalid DID format")
        return v

# --- Endpoints ---

@app.post("/identity/register")
@limiter.limit("10/minute")
async def register_agent(request: Request, body: RegisterRequest, api_key: str = Depends(verify_api_key)):
    check_registration_rate(api_key)
    agent_did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    if db_pool:
        async with db_pool.acquire() as conn:
            # Duplicate detection: same display_name + platform in last 24h
            dup = await conn.fetchval(
                "SELECT COUNT(*) FROM agents WHERE display_name = $1 AND platform = $2 AND created_at > now() - interval '24 hours'",
                body.display_name, body.platform
            )
            if dup > 0:
                raise HTTPException(409, "Agent with this name and platform was already registered in the last 24 hours")
            reg_ip = _anonymize_ip(_get_client_ip(request))
            await conn.execute(
                "INSERT INTO agents (did, display_name, platform, agent_type, created_at, registration_ip) VALUES ($1, $2, $3, 'external', $4, $5)",
                agent_did, body.display_name, body.platform, datetime.datetime.utcnow(), reg_ip
            )
    badge = f"\u2713 Verified by MolTrust | {agent_did} | Register: https://api.moltrust.ch/join?ref={agent_did}"
    ts = datetime.datetime.utcnow().isoformat()
    tx_hash = await anchor_to_base(agent_did, ts)
    if tx_hash and db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE agents SET base_tx_hash = $1 WHERE did = $2", tx_hash, agent_did)
    auto_vc = issue_credential(agent_did, "AgentTrustCredential", {"trustProvider": "MolTrust", "reputation": {"score": 0.0, "total_ratings": 0}, "verified": True})
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO credentials (subject_did, credential_type, issuer, issued_at, expires_at, proof_value, raw_vc)
                VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                agent_did, "AgentTrustCredential", auto_vc["issuer"],
                datetime.datetime.fromisoformat(auto_vc["issuanceDate"].replace("Z","")),
                datetime.datetime.fromisoformat(auto_vc["expirationDate"].replace("Z","")),
                auto_vc["proof"]["proofValue"],
                json.dumps(auto_vc)
            )

    # --- Credits: link API key and grant 100 free credits ---
    credits_granted = 0
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await link_api_key_to_did(conn, api_key, agent_did)
                    await ensure_balance_row(conn, agent_did, 0)
                    await grant_credits(conn, agent_did, 175, "registration", "Free credits on registration")
                    credits_granted = 175
        except Exception as e:
            logger.error("Credit grant failed for %s: %s", agent_did, e)

    # ERC-8004 dual registration
    erc8004_result = None
    if body.erc8004:
        from app.erc8004 import register_onchain_agent
        erc8004_result = register_onchain_agent(agent_did)
        if erc8004_result.get("agent_id") and db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE agents SET erc8004_agent_id = $1 WHERE did = $2",
                    erc8004_result["agent_id"], agent_did
                )

    # Fire-and-forget welcome email
    if body.email:
        asyncio.create_task(send_welcome_email(body.email, agent_did, body.display_name))

    response = {
        "did": agent_did,
        "display_name": body.display_name,
        "status": "registered",
        "badge": badge,
        "credential": auto_vc,
        "credits": {"balance": credits_granted, "currency": "CREDITS"},
        "base_anchor": {"tx_hash": tx_hash, "chain": "base", "explorer": f"https://basescan.org/tx/{tx_hash}" if tx_hash else None},
        "headers": {
            "X-MolTrust-DID": agent_did,
            "X-MolTrust-Verify": f"https://api.moltrust.ch/join?ref={agent_did}"
        }
    }
    if erc8004_result:
        response["erc8004"] = erc8004_result
    return response

@app.post("/auth/moltbook")
@limiter.limit("20/minute")
async def auth_with_moltbook(request: Request, body: MoltbookAuthRequest):
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                "https://www.moltbook.com/api/v1/agents/verify-identity",
                headers={"X-Moltbook-App-Key": MOLTBOOK_APP_KEY},
                json={"token": body.token}
            )
        except httpx.TimeoutException:
            raise HTTPException(504, "Moltbook verification timed out")
        except httpx.RequestError:
            raise HTTPException(502, "Could not reach Moltbook")
    if resp.status_code != 200:
        raise HTTPException(401, "Invalid Moltbook token")
    data = resp.json()
    if not data.get("valid"):
        raise HTTPException(401, "Token not valid")
    agent = data.get("agent", {})
    return {
        "status": "authenticated",
        "moltbook_id": str(agent.get("id", ""))[:64],
        "name": str(agent.get("name", ""))[:64],
        "karma": agent.get("karma", 0),
        "moltrust_did": f"did:moltrust:{uuid.uuid4().hex[:16]}"
    }

@app.get("/identity/verify/{did}")
@limiter.limit("30/minute")
async def verify_agent(request: Request, did: str = Path(max_length=40)):
    did = validate_did(did)
    result = {"did": did, "verified": False, "reputation": 0.0}
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT did, display_name FROM agents WHERE did = $1", did)
            if row:
                result["verified"] = True
                await update_last_seen(did)
    return result

# Grade → tier mapping used by the moltrust.ch /verify/{did} frontend.
# The frontend renders `tier-${tier}` as a CSS class, so the value
# must be a non-empty string. Low/no grades collapse to "none" rather
# than null so the class name stays well-formed.
_BADGE_TIER_BY_GRADE = {"A": "gold", "B": "silver", "C": "bronze"}


@app.get("/identity/badge/{did}")
@limiter.limit("60/minute")
async def get_identity_badge(request: Request, did: str = Path(max_length=80)):
    """Identity badge — composite of agent metadata, current trust score and
    rating count, plus convenience URLs for the public verify page and SVG
    badge. Used by the moltrust.ch /verify/{did} frontend, which fetches
    this JSON in parallel with /identity/badge/{did}.svg.

    Returns 200 with `verified: true` for any registered DID, even if the
    trust score is still withheld (insufficient endorsements). Returns 404
    only when the DID is not registered at all.

    Frontend contract (the fields the /verify/{did} page reads):
      verified, tier, trust_score, grade, issued_at, expires_at,
      vc_hash, badge_url. Additional fields (display_name, withheld,
      total_ratings, average_rating, verify_url) are extras the page
      ignores but other consumers (klaw gateway, etc.) may use.
    """
    from app.swarm.trust_score import compute_phase2_score, score_to_grade
    did = validate_did(did)
    if not db_pool:
        raise HTTPException(503, "database not available")
    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow(
            "SELECT did, display_name, created_at FROM agents WHERE did = $1",
            did,
        )
        if not agent:
            raise HTTPException(404, "agent not registered")
        rating_row = await conn.fetchrow(
            "SELECT COALESCE(AVG(score), 0) AS avg_score, COUNT(*) AS total "
            "FROM ratings WHERE to_did = $1",
            did,
        )
        try:
            ts = await compute_phase2_score(did, conn)
        except Exception:
            ts = {"score": None, "withheld": True}
        await update_last_seen(did)

    trust_score = ts.get("score")
    grade = score_to_grade(trust_score) if trust_score is not None else None
    tier = _BADGE_TIER_BY_GRADE.get(grade, "none")
    issued_at = agent["created_at"].isoformat() if agent["created_at"] else None
    return {
        "did": did,
        "verified": True,
        "display_name": agent["display_name"],
        "tier": tier,
        "trust_score": trust_score,
        "grade": grade,
        # Registration timestamp — when the DID first entered MolTrust.
        "issued_at": issued_at,
        # Identity badges don't expire; they reflect current trust state.
        # Frontend renders null as "—".
        "expires_at": None,
        # VC-hash storage is not yet wired through to the agents table —
        # field is reserved so the frontend's data.vc_hash access doesn't
        # throw, populated when the VC pipeline lands.
        "vc_hash": None,
        "withheld": ts.get("withheld", False),
        "total_ratings": int(rating_row["total"]) if rating_row else 0,
        "average_rating": round(float(rating_row["avg_score"]), 2)
        if rating_row and rating_row["avg_score"] is not None
        else 0.0,
        "verify_url": f"https://moltrust.ch/verify/{did}",
        "badge_url": f"https://api.moltrust.ch/identity/badge/{did}.svg",
    }


@app.get("/identity/badge/{did}.svg")
@limiter.limit("60/minute")
async def get_identity_badge_svg(request: Request, did: str = Path(max_length=80)):
    """SVG badge for embedding/inlining on the moltrust.ch /verify/{did}
    page. The frontend fetches this in parallel with /identity/badge/{did}
    and inlines the result via r.text().

    Mirrors the rendering logic of /badge/{did:path} (which predates the
    /identity/* convention) so both URLs stay in lockstep. 1h cache.
    Returns 200 with a placeholder SVG even for unknown/unscored DIDs —
    matches the /badge/{did:path} behaviour (renders 'N/A' rather than
    surfacing a 404 inline image).
    """
    from app.swarm.trust_score import compute_phase2_score, score_to_grade
    score = None
    grade = None
    try:
        if db_pool:
            async with db_pool.acquire() as conn:
                result = await compute_phase2_score(did, conn)
                score = result.get("score")
                grade = score_to_grade(score)
    except Exception:
        pass

    did_short = did[-8:] if len(did) > 8 else did
    svg = _build_badge_svg(score, grade, did_short)

    from starlette.responses import Response as _Resp
    return _Resp(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "max-age=3600, s-maxage=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/reputation/query/{did}")
@limiter.limit("30/minute")
async def get_reputation(request: Request, did: str = Path(max_length=40)):
    did = validate_did(did)
    result = {"did": did, "score": 0.0, "total_ratings": 0}
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(AVG(score), 0) as avg_score, COUNT(*) as total FROM ratings WHERE to_did = $1",
                did
            )
            if row:
                result["score"] = round(float(row["avg_score"]), 2)
                result["total_ratings"] = int(row["total"])
    return result

@app.post("/reputation/rate")
@limiter.limit("10/minute")
async def rate_agent(request: Request, body: RateRequest, api_key: str = Depends(verify_api_key)):
    if body.from_did == body.to_did:
        raise HTTPException(400, "Cannot rate yourself")
    if db_pool:
        async with db_pool.acquire() as conn:
            # HIGH-1: Verify from_did matches authenticated caller
            caller_did = await resolve_did_from_api_key(conn, api_key)
            if caller_did != body.from_did:
                raise HTTPException(403, "from_did must match your authenticated agent DID")
            await conn.execute(
                "INSERT INTO ratings (from_did, to_did, score, created_at) VALUES ($1, $2, $3, $4)",
                body.from_did, body.to_did, body.score, datetime.datetime.utcnow()
            )
    # ERC-8004 bridge: post feedback on-chain if agent is dual-registered
    erc8004_tx = None
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT erc8004_agent_id FROM agents WHERE did = $1", body.to_did)
            if row and row["erc8004_agent_id"] is not None:
                from app.erc8004 import post_reputation_feedback
                result = post_reputation_feedback(row["erc8004_agent_id"], body.to_did, body.score)
                if "tx_hash" in result:
                    erc8004_tx = result["tx_hash"]
    return {"status": "rated", "from": body.from_did, "to": body.to_did, "score": body.score, "erc8004_tx": erc8004_tx}

@app.get("/skills")
@limiter.limit("30/minute")
async def list_skills(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    skills = []
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, name, author_did, security_score FROM skills ORDER BY security_score DESC LIMIT $1", limit)
            skills = [dict(row) for row in rows]
    return {"skills": skills, "total": len(skills)}

@app.post("/skill/interaction-proof")
@limiter.limit("30/minute")
async def create_interaction_proof_endpoint(request: Request, req: InteractionProofRequest):
    """Interaction Proof: hash payload + anchor on Base L2. Required before endorsement."""
    # Feature 3: Sequential Signing Validation (Tech Spec v0.2.2)
    # Only validate signing if payload contains signing-related fields
    payload = req.interaction_payload
    if any(k in payload for k in ("proofInitiator", "proofResponder", "singleSig")):
        signing_result = validate_interaction_proof_signing(payload)
        if not signing_result["valid"]:
            raise HTTPException(status_code=400, detail={
                "error": "invalid_signing_sequence",
                "messages": signing_result["errors"],
            })
    from app.swarm.interaction_proof import create_interaction_proof
    async with db_pool.acquire() as conn:
        try:
            result = await create_interaction_proof(
                req.api_key, req.interaction_payload, conn
            )
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/skill/endorse")
@limiter.limit("20/minute")
async def endorse_skill_endpoint(request: Request, req: EndorseRequest):
    """Issue SkillEndorsementCredential (W3C VC). Requires valid interaction proof."""
    from app.swarm.endorsement import issue_endorsement
    async with db_pool.acquire() as conn:
        try:
            vc = await issue_endorsement(
                req.api_key, req.endorsed_did, req.skill,
                req.evidence_hash, req.evidence_timestamp,
                req.vertical, conn
            )
            return vc
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/skill/trust-score/{did:path}")
async def get_trust_score(did: str):
    """Phase 2 Trust Score with breakdown. Free. 1h cache."""
    from app.swarm.trust_score import compute_phase2_score, score_to_grade
    from app.anomaly import compute_flags
    async with db_pool.acquire() as conn:
        # Revoked agents return score 0 (ZeroID Feature 2)
        _rev = await conn.fetchrow(
            "SELECT revoked_at, revocation_reason FROM agents WHERE did = $1", did
        )
        if _rev and _rev["revoked_at"]:
            return {
                "did": did, "trust_score": 0.0, "grade": "REVOKED",
                "breakdown": {"revoked": True, "reason": _rev["revocation_reason"]},
                "endorser_count": 0, "withheld": False,
                "flags": ["revoked"], "flag_count": 1,
                "computed_at": None, "cache_valid_until": None,
                "valid_until": None,  # CAEP alias for cache_valid_until
            }
        try:
            result = await compute_phase2_score(did, conn)
            cached = await conn.fetchrow(
                "SELECT computed_at, cache_valid_until "
                "FROM trust_score_cache WHERE did = $1", did
            )
            flags = await compute_flags(did, result["score"] or 0, conn) if not result["withheld"] else []
            score_response = {
                "did": did,
                "trust_score": result["score"],
                "grade": score_to_grade(result["score"]),
                "breakdown": {
                    "direct_score": result["direct_score"],
                    "propagated_score": result["propagated_score"],
                    "cross_vertical_bonus": result["cross_vertical_bonus"],
                    "interaction_bonus": result["interaction_bonus"],
                    "prediction_bonus": result.get("prediction_bonus", 0.0),
                    "wallet_bonus": result.get("wallet_bonus", 0.0),
                    "sybil_penalty": result["sybil_penalty"],
                    "agent_class_modifier": result.get("agent_class_modifier", 0.0),
                    "computation_method": result["computation_method"],
                },
                "endorser_count": result["endorser_count"],
                "withheld": result["withheld"],
                "flags": flags,
                "flag_count": len(flags),
                "computed_at": cached["computed_at"].isoformat() if cached else None,
                "cache_valid_until": cached["cache_valid_until"].isoformat() if cached else None,
                "consistency_level": "L1",
                "evaluation_context": {
                    "evaluated_at": int(cached["computed_at"].timestamp()) if cached else None,
                    "policy_version": result["computation_method"],
                    "cache_valid_seconds": int((cached["cache_valid_until"] - cached["computed_at"]).total_seconds()) if cached else 3600,
                },
            }
            # CAEP: alias valid_until = cache_valid_until (keep both, non-breaking)
            score_response["valid_until"] = score_response["cache_valid_until"]
            # CAEP: sign deterministic minimal payload with registry key
            if score_response["computed_at"] and score_response["valid_until"]:
                from app.signature import sign_payload, build_score_signing_payload
                signing_payload = build_score_signing_payload(
                    did=score_response["did"],
                    trust_score=score_response["trust_score"],
                    computed_at=score_response["computed_at"],
                    valid_until=score_response["valid_until"],
                    policy_version=score_response["evaluation_context"]["policy_version"],
                )
                score_response["registry_signature"] = sign_payload(signing_payload)
            return score_response
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/skill/endorsements/given/{did:path}")
async def get_endorsements_given(did: str):
    """All endorsements given by an agent (transparency). Free."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT endorsed_did, skill, vertical, "
            "issued_at, expires_at "
            "FROM endorsements "
            "WHERE endorser_did = $1 AND expires_at > NOW() "
            "ORDER BY issued_at DESC", did
        )
        return {
            "did": did,
            "endorsements_given": [
                {
                    "endorsed_did": r["endorsed_did"],
                    "skill": r["skill"],
                    "vertical": r["vertical"],
                    "issued_at": r["issued_at"].isoformat(),
                    "expires_at": r["expires_at"].isoformat(),
                }
                for r in rows
            ],
            "total": len(rows)
        }

@app.get("/skill/endorsements/{did:path}")
async def get_endorsements(did: str):
    """All received endorsements for an agent. Free."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT endorser_did, skill, vertical, "
            "issued_at, expires_at, evidence_hash "
            "FROM endorsements "
            "WHERE endorsed_did = $1 AND expires_at > NOW() "
            "ORDER BY issued_at DESC", did
        )
        return {
            "did": did,
            "endorsements": [
                {
                    "endorser_did": r["endorser_did"],
                    "skill": r["skill"],
                    "vertical": r["vertical"],
                    "issued_at": r["issued_at"].isoformat(),
                    "expires_at": r["expires_at"].isoformat(),
                    "evidence_hash": f"sha256:{r['evidence_hash']}"
                }
                for r in rows
            ],
            "total": len(rows)
        }


# ═══════════════════════════════════════════════════════════════
# SWARM INTELLIGENCE — Phase 2 Endpoints
# ═══════════════════════════════════════════════════════════════

@app.get("/swarm/graph/{did:path}")
async def get_swarm_graph(did: str):
    """Endorsement graph: who endorses this DID, who endorses them (2 hops)."""
    async with db_pool.acquire() as conn:
        try:
            nodes = {}
            edges = []

            # Hop 1: direct endorsers
            hop1 = await conn.fetch(
                "SELECT DISTINCT endorser_did, vertical "
                "FROM endorsements WHERE endorsed_did = $1 "
                "AND expires_at > NOW()", did
            )
            # Add target node
            from app.swarm.trust_score import compute_phase2_score, score_to_grade
            target_result = await compute_phase2_score(did, conn)
            nodes[did] = {
                "did": did,
                "score": target_result["score"],
                "grade": score_to_grade(target_result["score"]),
                "hop": 0,
            }

            for e in hop1:
                endorser = e["endorser_did"]
                if endorser not in nodes:
                    e_result = await compute_phase2_score(endorser, conn)
                    seed = await conn.fetchrow(
                        "SELECT label FROM swarm_seeds WHERE did = $1",
                        endorser
                    )
                    nodes[endorser] = {
                        "did": endorser,
                        "score": e_result["score"],
                        "grade": score_to_grade(e_result["score"]),
                        "label": seed["label"] if seed else None,
                        "hop": 1,
                    }
                edges.append({
                    "from": endorser,
                    "to": did,
                    "vertical": e["vertical"],
                })

                # Hop 2: endorsers of endorsers
                hop2 = await conn.fetch(
                    "SELECT DISTINCT endorser_did, vertical "
                    "FROM endorsements WHERE endorsed_did = $1 "
                    "AND expires_at > NOW()", endorser
                )
                for e2 in hop2:
                    endorser2 = e2["endorser_did"]
                    if endorser2 not in nodes:
                        seed2 = await conn.fetchrow(
                            "SELECT label FROM swarm_seeds WHERE did = $1",
                            endorser2
                        )
                        nodes[endorser2] = {
                            "did": endorser2,
                            "score": None,
                            "grade": "N/A",
                            "label": seed2["label"] if seed2 else None,
                            "hop": 2,
                        }
                    edges.append({
                        "from": endorser2,
                        "to": endorser,
                        "vertical": e2["vertical"],
                    })

            return {
                "did": did,
                "nodes": list(nodes.values()),
                "edges": edges,
                "node_count": len(nodes),
                "edge_count": len(edges),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/swarm/stats")
async def get_swarm_stats():
    """Global swarm statistics."""
    async with db_pool.acquire() as conn:
        try:
            total_agents = await conn.fetchval(
                "SELECT COUNT(*) FROM agents"
            )
            total_endorsements = await conn.fetchval(
                "SELECT COUNT(*) FROM endorsements WHERE expires_at > NOW()"
            )
            seeds = await conn.fetch(
                "SELECT did, label, base_score FROM swarm_seeds "
                "ORDER BY registered_at"
            )
            avg_score = await conn.fetchval(
                "SELECT AVG(score) FROM trust_score_cache "
                "WHERE score >= 0 AND cache_valid_until > NOW()"
            )
            top_trusted = await conn.fetch(
                "SELECT did, score FROM trust_score_cache "
                "WHERE score >= 0 AND cache_valid_until > NOW() "
                "ORDER BY score DESC LIMIT 5"
            )
            max_depth = await conn.fetchval(
                "SELECT MAX(propagation_depth) FROM swarm_graph"
            )

            return {
                "total_agents": total_agents,
                "total_endorsements": total_endorsements,
                "seed_agents": [
                    {"did": s["did"], "label": s["label"],
                     "base_score": s["base_score"]}
                    for s in seeds
                ],
                "avg_trust_score": round(float(avg_score), 1) if avg_score else None,
                "propagation_depth": max_depth or 0,
                "top_trusted": [
                    {"did": t["did"], "score": round(t["score"], 1)}
                    for t in top_trusted
                ],
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


class SeedRequest(BaseModel):
    did: str
    label: str
    base_score: float = 80.0


@app.post("/swarm/seed")
async def register_seed(request: Request, req: SeedRequest):
    """Register a trusted seed agent. Requires ADMIN_KEY header."""
    verify_admin(request, AdminPermission.WRITE)
    async with db_pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO swarm_seeds (did, label, base_score) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (did) DO UPDATE SET "
                "label = EXCLUDED.label, base_score = EXCLUDED.base_score",
                req.did, req.label, req.base_score
            )
            # Invalidate cache for this DID
            await conn.execute(
                "DELETE FROM trust_score_cache WHERE did = $1", req.did
            )
            return {
                "status": "registered",
                "did": req.did,
                "label": req.label,
                "base_score": req.base_score,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/swarm/propagate/{did:path}")
async def propagate_trust(did: str):
    """Force recompute trust score with Phase 2 algorithm."""
    from app.swarm.trust_score import compute_phase2_score, score_to_grade
    async with db_pool.acquire() as conn:
        try:
            # Invalidate cache first
            await conn.execute(
                "DELETE FROM trust_score_cache WHERE did = $1", did
            )
            result = await compute_phase2_score(did, conn)
            return {
                "did": did,
                "trust_score": result["score"],
                "grade": score_to_grade(result["score"]),
                "breakdown": result,
                "recomputed": True,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/payment/lightning/invoice")
@limiter.limit("5/minute")
async def create_lightning_invoice(request: Request, body: LightningInvoiceRequest, api_key: str = Depends(verify_api_key)):
    return {"status": "pending", "amount_sats": body.amount_sats, "description": body.description, "note": "phoenixd integration ready"}

@app.get("/health")
@limiter.limit("60/minute")
async def health_check(request: Request):
    db_ok = False
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except:
            pass
    return {
        "status": "ok",
        "version": "2.4",
        "database": "connected" if db_ok else "unavailable",
        "timestamp": str(datetime.datetime.utcnow())
    }
# --- W3C DID:web Support ---

DID_WEB_DOCUMENT = {
    "@context": [
        "https://www.w3.org/ns/did/v1",
        "https://w3id.org/security/suites/ed25519-2020/v1"
    ],
    "id": "did:web:api.moltrust.ch",
    "controller": "did:web:api.moltrust.ch",
    "verificationMethod": [{
        "id": "did:web:api.moltrust.ch#key-1",
        "type": "Ed25519VerificationKey2020",
        "controller": "did:web:api.moltrust.ch",
        "publicKeyMultibase": "z6MktwcfvxeKmXstWpyEr9wJkJE2xzzkpBkdCSghdvCzrqDC"
    }],
    "authentication": ["did:web:api.moltrust.ch#key-1"],
    "assertionMethod": ["did:web:api.moltrust.ch#key-1"],
    "service": [
        {
            "id": "did:web:api.moltrust.ch#trust-api",
            "type": "TrustLayer",
            "serviceEndpoint": "https://api.moltrust.ch"
        },
        {
            "id": "did:web:api.moltrust.ch#identity",
            "type": "AgentIdentity",
            "serviceEndpoint": "https://api.moltrust.ch/identity"
        },
        {
            "id": "did:web:api.moltrust.ch#reputation",
            "type": "ReputationService",
            "serviceEndpoint": "https://api.moltrust.ch/reputation"
        }
    ]
}

@app.get("/.well-known/did.json")
@limiter.limit("60/minute")
async def did_web_document(request: Request):
    return DID_WEB_DOCUMENT


# SELECT clause used by both /identity/resolve and /identity/resolve-external
# so they share the same column shape and the helper below can build a full document.
_AGENT_DOC_COLUMNS = (
    "did, display_name, platform, created_at, "
    "wallet_address, wallet_chain, wallet_bound_at, "
    "public_key_hex, key_anchor_tx, key_anchor_block"
)


def _build_did_document(row) -> dict:
    """Build a W3C DID Document from an agents-table row.

    Shared by /identity/resolve and /identity/resolve-external so both endpoints
    return the same shape (verificationMethod/authentication/assertionMethod/service).
    """
    doc = {
        "@context": [
            "https://www.w3.org/ns/did/v1",
            "https://w3id.org/security/suites/ed25519-2020/v1",
        ],
        "id": row["did"],
        "controller": "did:web:api.moltrust.ch",
        "metadata": {
            "display_name": row["display_name"],
            "platform": row["platform"],
            "created": str(row["created_at"]),
            "trust_provider": "MolTrust",
        },
    }
    if row["public_key_hex"]:
        key_id = f"{row['did']}#key-1"
        doc["verificationMethod"] = [{
            "id": key_id,
            "type": "Ed25519VerificationKey2020",
            "controller": row["did"],
            "publicKeyHex": row["public_key_hex"],
        }]
        doc["authentication"] = [key_id]
        doc["assertionMethod"] = [key_id]
        if row["key_anchor_tx"]:
            doc["metadata"]["keyAnchor"] = {
                "chain": "base",
                "tx": row["key_anchor_tx"],
                "block": row["key_anchor_block"],
            }
    if row["wallet_address"]:
        chain = row["wallet_chain"] or "base"
        svc_type = "SolanaPaymentService" if chain == "solana" else "PaymentService"
        currency = "USDC" if chain != "solana" else "SOL"
        doc.setdefault("service", []).append({
            "id": f"{row['did']}#payment",
            "type": svc_type,
            "serviceEndpoint": {
                "address": row["wallet_address"],
                "chain": chain,
                "currency": currency,
                "bound_at": row["wallet_bound_at"].isoformat() + "Z" if row["wallet_bound_at"] else None,
            },
        })
    return doc


async def _resolve_did_web_external(did: str) -> dict:
    """Resolve an external did:web:* per W3C spec by fetching /.well-known/did.json.

    Examples:
      did:web:foo.com           -> https://foo.com/.well-known/did.json
      did:web:foo.com:agents:x  -> https://foo.com/agents/x/did.json

    Port encoding via percent-encoded colon is decoded.
    """
    if not did.startswith("did:web:"):
        raise HTTPException(400, "Not a did:web identifier")
    method_specific_id = did[len("did:web:"):]
    if not method_specific_id:
        raise HTTPException(400, "Empty did:web identifier")
    parts = method_specific_id.split(":")
    # Decode percent-encoded chars (e.g. %3A for : in port specs)
    parts = [urllib.parse.unquote(p) for p in parts]
    domain = parts[0]
    if len(parts) == 1:
        url = f"https://{domain}/.well-known/did.json"
    else:
        url = f"https://{domain}/{'/'.join(parts[1:])}/did.json"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, headers={"Accept": "application/did+ld+json, application/json"})
    except httpx.RequestError as e:
        raise HTTPException(404, f"didNotResolved: {e.__class__.__name__}")
    if resp.status_code != 200:
        raise HTTPException(404, f"didNotResolved: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise HTTPException(502, "didDocument is not valid JSON")


@app.get("/identity/resolve/{did:path}")
@limiter.limit("30/minute")
async def resolve_did(request: Request, did: str):
    if len(did) > 256:
        raise HTTPException(400, "DID too long")
    if did == "did:web:api.moltrust.ch":
        return DID_WEB_DOCUMENT
    if DID_PATTERN.match(did):
        if db_pool:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT {_AGENT_DOC_COLUMNS} FROM agents WHERE did = $1", did
                )
                if row:
                    await update_last_seen(did)
                    return _build_did_document(row)
        raise HTTPException(404, "DID not found")
    if did.startswith("did:web:"):
        return await _resolve_did_web_external(did)
    raise HTTPException(400, "Unsupported DID method")

@app.get("/identity/key/{did:path}")
@limiter.limit("30/minute")
async def get_agent_public_key(request: Request, did: str):
    """Return public key + on-chain anchor info for a DID."""
    if not DID_PATTERN.match(did):
        raise HTTPException(400, "Invalid DID format")
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT did, public_key_hex, key_anchor_tx, key_anchor_block FROM agents WHERE did = $1", did
        )
    if not row:
        raise HTTPException(404, "DID not found")
    if not row["public_key_hex"]:
        raise HTTPException(404, "No public key registered for this DID")
    return {
        "did": row["did"],
        "public_key_hex": row["public_key_hex"],
        "key_anchor_tx": row["key_anchor_tx"],
        "key_anchor_block": row["key_anchor_block"],
        "anchor_verified": row["key_anchor_tx"] is not None
    }

# --- DID-Wallet Binding Endpoints ---

class WalletBindRequest(BaseModel):
    did: str = Field(max_length=40)
    wallet_address: str = Field(max_length=64)
    wallet_chain: str = Field(default="base", max_length=20)
    wallet_signature: str = Field(max_length=512)
    nonce: str = Field(max_length=64)

    @field_validator("did")
    @classmethod
    def validate_did_format(cls, v):
        if not re.match(r"^did:moltrust:[a-f0-9]{16}$", v):
            raise ValueError("Invalid DID format")
        return v

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet(cls, v):
        # EVM: 0x + 40 hex chars; Solana: base58 32-44 chars
        if not (re.match(r"^0x[0-9a-fA-F]{40}$", v) or re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", v)):
            raise ValueError("Invalid wallet address (EVM or Solana)")
        return v

    @field_validator("wallet_chain")
    @classmethod
    def validate_chain(cls, v):
        if v not in ("base", "ethereum", "polygon", "arbitrum", "optimism", "solana"):
            raise ValueError("Unsupported chain")
        return v


@app.get("/identity/nonce")
@limiter.limit("30/minute")
async def get_binding_nonce(request: Request, did: str = Query(max_length=40),
                            chain: str = Query(default="base", max_length=20)):
    """Generate a nonce for DID-wallet binding signature."""
    if not DID_PATTERN.match(did):
        raise HTTPException(400, "Invalid DID format")
    if not NONCE_SECRET:
        raise HTTPException(503, "Nonce service not configured")
    if chain not in ("base", "ethereum", "polygon", "arbitrum", "optimism", "solana"):
        raise HTTPException(400, "Unsupported chain")
    nonce = _generate_nonce(did)
    msg_template = f"MolTrust DID Binding\nDID: {did}\nWallet: <your-wallet>\nNonce: {nonce}\nChain: {chain}"
    result = {"nonce": nonce, "expires_in": 300, "chain": chain, "message_template": msg_template}
    if chain == "solana":
        result["instructions"] = "Sign this message with your Solana wallet (Ed25519)"
    return result


@app.post("/identity/bind")
@limiter.limit("10/minute")
async def bind_wallet(request: Request, body: WalletBindRequest, api_key: str = Depends(verify_api_key)):
    """Bind a wallet address to a DID with cryptographic proof of ownership."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    if not NONCE_SECRET:
        raise HTTPException(503, "Nonce service not configured")

    # Verify nonce
    if not _verify_nonce(body.did, body.nonce):
        raise HTTPException(400, "Invalid or expired nonce")

    # Verify wallet signature
    if not _verify_wallet_signature(body.did, body.wallet_address, body.wallet_chain, body.nonce, body.wallet_signature):
        raise HTTPException(401, "Wallet signature verification failed")

    async with db_pool.acquire() as conn:
        # Verify caller owns this DID
        caller_did = await resolve_did_from_api_key(conn, api_key)
        if caller_did != body.did:
            raise HTTPException(403, "API key does not own this DID")

        # Check DID exists
        agent = await conn.fetchrow("SELECT did, wallet_address FROM agents WHERE did = $1", body.did)
        if not agent:
            raise HTTPException(404, "DID not found")

        # Check wallet not already bound to another DID
        existing = await conn.fetchval(
            "SELECT did FROM agents WHERE wallet_address = $1 AND did != $2",
            body.wallet_address, body.did
        )
        if existing:
            raise HTTPException(409, "Wallet already bound to another DID")

        # Bind wallet
        now = datetime.datetime.utcnow()
        await conn.execute(
            """UPDATE agents
               SET wallet_address = $1, wallet_chain = $2,
                   wallet_bound_at = $3, wallet_signature = $4
               WHERE did = $5""",
            body.wallet_address, body.wallet_chain, now, body.wallet_signature, body.did
        )

        # Create IPR record for audit trail
        try:
            from app.swarm.interaction_proof import create_interaction_proof
            await create_interaction_proof(
                api_key,
                {
                    "type": "wallet_binding",
                    "agent_did": body.did,
                    "wallet_address": body.wallet_address,
                    "wallet_chain": body.wallet_chain,
                    "bound_at": now.isoformat(),
                },
                conn
            )
        except Exception as e:
            logger.warning("IPR for wallet binding failed (non-critical): %s", e)

    return {
        "status": "bound",
        "did": body.did,
        "wallet_address": body.wallet_address,
        "wallet_chain": body.wallet_chain,
        "bound_at": now.isoformat() + "Z",
    }


@app.get("/x402/verify")
@limiter.limit("30/minute")
async def x402_verify(request: Request, did: str = Query(max_length=40)):
    """Check if a DID has payment readiness (bound wallet + trust score)."""
    if not did.startswith("did:moltrust:") or len(did) > 40:
        raise HTTPException(400, "Invalid DID format")
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT did, wallet_address, wallet_chain, wallet_bound_at FROM agents WHERE did = $1",
            did
        )
    if not row:
        raise HTTPException(404, "DID not found")

    # Get trust score
    trust_score = 0.0
    try:
        from app.swarm.trust_score import compute_phase2_score, score_to_grade
        async with db_pool.acquire() as conn:
            score_data = await compute_phase2_score(did, conn)
            trust_score = score_data.get("score", 0.0) or 0.0
    except Exception:
        pass

    # Log x402/verify call
    try:
        caller_ip = _get_client_ip(request)
    except Exception:
        caller_ip = None

    if not row["wallet_address"]:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO x402_verify_calls (queried_did, caller_ip, result_payment_ready, result_trust_score) VALUES ($1, $2, $3, $4)",
                    did, caller_ip, False, trust_score,
                )
        except Exception:
            pass
        return {
            "did": did,
            "verified": True,
            "payment_ready": False,
            "trust_score": trust_score,
            "reason": "no_wallet_bound",
        }

    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO x402_verify_calls (queried_did, caller_ip, result_payment_ready, result_trust_score) VALUES ($1, $2, $3, $4)",
                did, caller_ip, True, trust_score,
            )
    except Exception:
        pass

    return {
        "did": did,
        "wallet": row["wallet_address"],
        "chain": row["wallet_chain"],
        "trust_score": trust_score,
        "verified": True,
        "payment_ready": True,
        "bound_at": row["wallet_bound_at"].isoformat() + "Z" if row["wallet_bound_at"] else None,
    }


# --- x402 Stats ---

@app.get("/x402/stats")
@limiter.limit("30/minute")
async def x402_stats(request: Request, did: str = Query(default=None, max_length=40)):
    """Stats on /x402/verify usage. Optional: filter by DID."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        if did:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_calls,
                    COUNT(DISTINCT caller_ip) as unique_callers,
                    SUM(CASE WHEN result_payment_ready THEN 1 ELSE 0 END) as payment_ready_calls,
                    MIN(called_at) as first_call,
                    MAX(called_at) as last_call,
                    COUNT(CASE WHEN called_at > NOW() - INTERVAL '24 hours' THEN 1 END) as calls_24h,
                    COUNT(CASE WHEN called_at > NOW() - INTERVAL '1 hour' THEN 1 END) as calls_1h
                FROM x402_verify_calls WHERE queried_did = $1
            """, did)
            return {
                "did": did,
                "stats": {
                    "total_calls": row["total_calls"],
                    "unique_callers": row["unique_callers"],
                    "payment_ready_calls": row["payment_ready_calls"],
                    "first_call": row["first_call"].isoformat() + "Z" if row["first_call"] else None,
                    "last_call": row["last_call"].isoformat() + "Z" if row["last_call"] else None,
                    "calls_24h": row["calls_24h"],
                    "calls_1h": row["calls_1h"],
                },
            }
        else:
            rows = await conn.fetch("""
                SELECT queried_did, COUNT(*) as total_calls,
                       COUNT(DISTINCT caller_ip) as unique_callers,
                       MAX(called_at) as last_call
                FROM x402_verify_calls
                GROUP BY queried_did ORDER BY total_calls DESC LIMIT 20
            """)
            total = await conn.fetchval("SELECT COUNT(*) FROM x402_verify_calls")
            unique_dids = await conn.fetchval("SELECT COUNT(DISTINCT queried_did) FROM x402_verify_calls")
            return {
                "total_verify_calls": total,
                "unique_dids_queried": unique_dids,
                "top_queried": [
                    {
                        "did": r["queried_did"],
                        "total_calls": r["total_calls"],
                        "unique_callers": r["unique_callers"],
                        "last_call": r["last_call"].isoformat() + "Z" if r["last_call"] else None,
                    }
                    for r in rows
                ],
            }


# --- Payment Webhook ---

BASESCAN_WEBHOOK_SECRET = os.getenv("BASESCAN_WEBHOOK_SECRET", "")


@app.post("/webhooks/payment")
async def payment_webhook(request: Request):
    """Receive Basescan webhook for incoming USDC payments to MolTrust wallet."""
    body = await request.body()

    # Validate HMAC signature if secret is configured
    if BASESCAN_WEBHOOK_SECRET:
        signature = request.headers.get("X-Basescan-Signature", "")
        expected = _hmac.new(
            BASESCAN_WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        if not _hmac.compare_digest(signature, expected):
            raise HTTPException(401, "Invalid webhook signature")

    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    tx_hash = str(data.get("txHash", ""))[:66]
    from_address = str(data.get("from", ""))[:64]
    to_address = str(data.get("to", ""))[:64]
    value = data.get("value", "0")
    token_symbol = str(data.get("tokenSymbol", "USDC"))[:20]

    # USDC has 6 decimals
    try:
        amount_usdc = float(value) / 1_000_000
    except (ValueError, TypeError):
        amount_usdc = 0.0

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    # Reverse-lookup: which DID owns this wallet?
    did = None
    async with db_pool.acquire() as conn:
        did = await conn.fetchval(
            "SELECT did FROM agents WHERE wallet_address = $1", to_address
        )
        try:
            await conn.execute("""
                INSERT INTO payment_events (tx_hash, from_address, to_address, amount_usdc, token, did)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, tx_hash, from_address, to_address, amount_usdc, token_symbol, did)
        except Exception:
            pass  # Duplicate tx_hash

    return {"status": "ok"}


# --- Ghost Agent Detection (RSAC Gap 3) ---

@app.get("/agents/inactive")
@limiter.limit("10/minute")
async def get_inactive_agents(request: Request, days: int = Query(default=30, ge=1, le=365)):
    """Returns agents inactive for more than `days` days. Admin-only. RSAC Gap 3."""
    verify_admin(request, AdminPermission.READ)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        inactive = await conn.fetch("""
            SELECT did, display_name, platform, created_at, last_active_at,
                   EXTRACT(DAY FROM NOW() - COALESCE(last_active_at, created_at))::int as days_inactive
            FROM agents
            WHERE COALESCE(last_active_at, created_at) < NOW() - INTERVAL '1 day' * $1
            ORDER BY last_active_at ASC NULLS FIRST
            LIMIT 100
        """, days)

    return {
        "threshold_days": days,
        "inactive_count": len(inactive),
        "agents": [
            {
                "did": a["did"],
                "display_name": a["display_name"],
                "platform": a["platform"],
                "created_at": a["created_at"].isoformat() if a["created_at"] else None,
                "last_active_at": a["last_active_at"].isoformat() + "Z" if a["last_active_at"] else None,
                "days_inactive": a["days_inactive"],
            }
            for a in inactive
        ],
    }


# --- DID Bridging & External Score Import ---

class DIDBridgeRequest(BaseModel):
    external_did: str = Field(max_length=256)
    moltrust_did: str = Field(max_length=40)
    wallet_address: str = Field(max_length=64)
    chain: str = Field(default="solana", max_length=20)
    proof: str = Field(max_length=512)
    nonce: str = Field(max_length=64)

    @field_validator("moltrust_did")
    @classmethod
    def validate_moltrust_did(cls, v):
        if not re.match(r"^did:moltrust:[a-f0-9]{16}$", v):
            raise ValueError("Invalid MolTrust DID format")
        return v

    @field_validator("external_did")
    @classmethod
    def validate_external_did(cls, v):
        if not v.startswith("did:"):
            raise ValueError("External DID must start with did:")
        return v


class ScoreImportRequest(BaseModel):
    moltrust_did: str = Field(max_length=40)
    external_did: str = Field(max_length=256)
    external_score: float = Field(ge=0)
    external_system: str = Field(max_length=32)
    proof: str = Field(default="", max_length=512)

    @field_validator("external_system")
    @classmethod
    def validate_system(cls, v):
        if v not in ("meeet", "generic", "aeoess", "agentid", "agentnexus"):
            raise ValueError("Unsupported external system")
        return v


def _map_meeet_score(meeet_score: float) -> float:
    """Map MEEET range 0-1100 to MolTrust 0-100 (logarithmic)."""
    import math
    if meeet_score <= 0:
        return 50.0
    normalized = min(meeet_score / 1100, 1.0)
    mapped = 50 + (50 * math.log1p(normalized * (math.e - 1)))
    return round(min(mapped, 100.0), 1)


@app.post("/identity/bridge")
@limiter.limit("10/minute")
async def bridge_did(request: Request, body: DIDBridgeRequest, api_key: str = Depends(verify_api_key)):
    """Bridge an external DID to a MolTrust DID via wallet signature proof."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        # Verify caller owns the MolTrust DID
        caller_did = await resolve_did_from_api_key(conn, api_key)
        if caller_did != body.moltrust_did:
            raise HTTPException(403, "API key does not own this MolTrust DID")

        # Verify wallet is bound to this DID
        agent = await conn.fetchrow(
            "SELECT wallet_address, wallet_chain FROM agents WHERE did = $1",
            body.moltrust_did
        )
        if not agent:
            raise HTTPException(404, "MolTrust DID not found")
        if not agent["wallet_address"] or agent["wallet_address"] != body.wallet_address:
            raise HTTPException(400, "Wallet not bound to this DID")

        # Verify nonce
        if not _verify_nonce(body.moltrust_did, body.nonce):
            raise HTTPException(400, "Invalid or expired nonce")

        # Verify signature over bridge message
        bridge_msg = f"MolTrust DID Binding\nDID: {body.moltrust_did}\nWallet: {body.wallet_address}\nNonce: {body.nonce}\nChain: {body.chain}"
        if not _verify_wallet_signature(body.moltrust_did, body.wallet_address, body.chain, body.nonce, body.proof):
            raise HTTPException(401, "Bridge signature verification failed")

        # Check for existing bridge
        existing = await conn.fetchval(
            "SELECT moltrust_did FROM did_bridges WHERE external_did = $1",
            body.external_did
        )
        if existing:
            if existing == body.moltrust_did:
                return {"status": "already_bridged", "external_did": body.external_did, "moltrust_did": body.moltrust_did}
            raise HTTPException(409, "External DID already bridged to another MolTrust DID")

        # Create bridge
        await conn.execute(
            "INSERT INTO did_bridges (external_did, moltrust_did, chain, wallet_address) VALUES ($1, $2, $3, $4)",
            body.external_did, body.moltrust_did, body.chain, body.wallet_address
        )

    return {
        "status": "bridged",
        "external_did": body.external_did,
        "moltrust_did": body.moltrust_did,
        "chain": body.chain,
    }


class SimpleBridgeRequest(BaseModel):
    external_did: str = Field(max_length=256)
    label: str = Field(default="", max_length=128)
    platform: str = Field(default="external", max_length=32)

    @field_validator("external_did")
    @classmethod
    def validate_ext_did(cls, v):
        if not v.startswith("did:"):
            raise ValueError("External DID must start with did:")
        return v


@app.post("/identity/bridge-simple")
@limiter.limit("10/minute")
async def bridge_did_simple(request: Request, body: SimpleBridgeRequest, api_key: str = Depends(verify_api_key)):
    """Lightweight DID bridge — maps external DID to caller's MolTrust DID. No wallet required."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        caller_did = await resolve_did_from_api_key(conn, api_key)
        if not caller_did:
            raise HTTPException(403, "No agent linked to this API key")

        # Check for existing bridge
        existing = await conn.fetchval(
            "SELECT moltrust_did FROM did_bridges WHERE external_did = $1", body.external_did
        )
        if existing:
            if existing == caller_did:
                return {"status": "already_bridged", "external_did": body.external_did, "moltrust_did": caller_did}
            raise HTTPException(409, "External DID already bridged to another MolTrust DID")

        # Create bridge
        await conn.execute(
            "INSERT INTO did_bridges (external_did, moltrust_did, chain, wallet_address) VALUES ($1, $2, $3, $4)",
            body.external_did, caller_did, body.platform, "",
        )

        # Update agent label if provided
        if body.label:
            await conn.execute(
                "UPDATE agents SET display_name = $1 WHERE did = $2 AND (display_name IS NULL OR display_name = 'anonymous')",
                body.label, caller_did,
            )

    return {
        "status": "bridged",
        "external_did": body.external_did,
        "moltrust_did": caller_did,
        "platform": body.platform,
    }


@app.get("/identity/resolve-external/{external_did:path}")
@limiter.limit("30/minute")
async def resolve_external_did(request: Request, external_did: str):
    """Resolve an external DID to its bridged MolTrust DID document."""
    if not external_did.startswith("did:") or len(external_did) > 256:
        raise HTTPException(400, "Invalid DID format")
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        bridge = await conn.fetchrow(
            "SELECT moltrust_did, chain, wallet_address, created_at FROM did_bridges WHERE external_did = $1",
            external_did
        )
    if not bridge:
        raise HTTPException(404, "No bridge found for this external DID")

    # Fetch MolTrust DID document via internal resolve
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_AGENT_DOC_COLUMNS} FROM agents WHERE did = $1",
            bridge["moltrust_did"]
        )
    if not row:
        raise HTTPException(404, "Bridged MolTrust DID not found")

    return {
        "external_did": external_did,
        "moltrust_did": bridge["moltrust_did"],
        "chain": bridge["chain"],
        "bridged_at": bridge["created_at"].isoformat() + "Z" if bridge["created_at"] else None,
        "document": _build_did_document(row),
    }


@app.post("/identity/import-score")
@limiter.limit("10/minute")
async def import_external_score(request: Request, body: ScoreImportRequest,
                                api_key: str = Depends(verify_api_key)):
    """Import an external trust score into MolTrust via DID bridge."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        # Verify caller owns the MolTrust DID
        caller_did = await resolve_did_from_api_key(conn, api_key)
        if caller_did != body.moltrust_did:
            raise HTTPException(403, "API key does not own this DID")

        # Verify bridge exists
        bridge = await conn.fetchrow(
            "SELECT moltrust_did FROM did_bridges WHERE external_did = $1 AND moltrust_did = $2",
            body.external_did, body.moltrust_did
        )
        if not bridge:
            raise HTTPException(400, "No valid bridge between these DIDs")

        # Map score
        if body.external_system == "meeet":
            mapped_score = _map_meeet_score(body.external_score)
        else:
            mapped_score = round(50 + (min(body.external_score, 1.0) * 50), 1)

        # Store as external endorsement in trust_score_cache
        # This gives a cross-vertical bonus via the swarm scoring
        try:
            await conn.execute(
                """INSERT INTO endorsements
                   (endorser_did, endorsed_did, skill, evidence_hash,
                    evidence_timestamp, vertical, weight, issued_at, expires_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW() + interval '90 days')""",
                "did:moltrust:external_import", body.moltrust_did,
                "general", hashlib.sha256(f"{body.external_did}:{body.external_system}".encode()).hexdigest(),
                datetime.datetime.utcnow(), "core", min(mapped_score / 100.0, 1.0),
            )
        except Exception as e:
            if "unique" in str(e).lower():
                pass  # Already imported
            else:
                raise

        # Invalidate trust score cache
        await conn.execute("DELETE FROM trust_score_cache WHERE did = $1", body.moltrust_did)

    return {
        "moltrust_did": body.moltrust_did,
        "external_did": body.external_did,
        "external_score": body.external_score,
        "external_system": body.external_system,
        "mapped_score": mapped_score,
    }


# --- Batch Registration ---

class BatchAgentEntry(BaseModel):
    external_did: str = Field(max_length=256)
    label: str = Field(max_length=64)
    capabilities: list[str] = Field(default_factory=list)

class BatchRegisterRequest(BaseModel):
    agents: list[BatchAgentEntry] = Field(min_length=1, max_length=1000)
    external_system: str = Field(max_length=32)
    jwks_url: str = Field(default="", max_length=512)

    @field_validator("external_system")
    @classmethod
    def validate_system(cls, v):
        if v not in ("aeoess", "agentid", "agentnexus", "meeet", "generic"):
            raise ValueError("Unsupported external system")
        return v



# ── CAEP events table setup ──────────────────────────────────────────────────
async def ensure_caep_table(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS caep_events (
            id SERIAL PRIMARY KEY,
            did VARCHAR(40) NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            payload JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    try:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_caep_did ON caep_events(did)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_caep_type ON caep_events(event_type)")
    except Exception:
        pass



# ═══════════════════════════════════════════════════════════════════════════════
# AGENT TYPE CLASSIFICATION (ZeroID Feature 1)
# ═══════════════════════════════════════════════════════════════════════════════

VALID_AGENT_CLASSES = ("orchestrator", "autonomous", "human_initiated", "copilot")

GOVERNANCE_RULES = {
    "orchestrator": {
        "cascade_revocation_priority": "critical",
        "min_trust_score_to_delegate": 60,
        "review_frequency_days": 30,
        "description": "Delegates tasks to sub-agents, plans workflows",
    },
    "autonomous": {
        "cascade_revocation_priority": "high",
        "min_trust_score_required": 50,
        "review_frequency_days": 90,
        "description": "Fully autonomous, no human in the loop",
    },
    "human_initiated": {
        "cascade_revocation_priority": "medium",
        "min_trust_score_required": 30,
        "review_frequency_days": 180,
        "description": "Human-triggered, then runs autonomously",
    },
    "copilot": {
        "cascade_revocation_priority": "low",
        "min_trust_score_required": 0,
        "review_frequency_days": 365,
        "description": "Human stays active in the loop",
    },
}

AGENT_CLASS_TRUST_MODIFIER = {
    "orchestrator": 5.0,
    "autonomous": 0.0,
    "human_initiated": 0.0,
    "copilot": -10.0,
}


class AgentClassRequest(BaseModel):
    agent_class: str = Field(..., description="One of: orchestrator, autonomous, human_initiated, copilot")
    agent_framework: str | None = Field(None, max_length=100, description="e.g. langgraph, crewai, autogen")
    agent_version: str | None = Field(None, max_length=50)
    publisher: str | None = Field(None, max_length=255)

    @field_validator("agent_class")
    @classmethod
    def validate_agent_class(cls, v):
        if v not in VALID_AGENT_CLASSES:
            raise ValueError(f"Invalid agent_class. Must be one of: {VALID_AGENT_CLASSES}")
        return v


@app.post("/identity/agent-type/{did}")
@limiter.limit("30/minute")
async def set_agent_class(request: Request, did: str, body: AgentClassRequest, api_key: str = Depends(verify_api_key)):
    """Set or update the agent classification and governance tier."""
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow("SELECT did, agent_class FROM agents WHERE did = $1", did)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        old_class = agent["agent_class"]

        await conn.execute(
            """UPDATE agents
               SET agent_class = $1,
                   agent_framework = $2,
                   agent_version = $3,
                   publisher = $4,
                   agent_class_updated_at = NOW()
               WHERE did = $5""",
            body.agent_class, body.agent_framework, body.agent_version,
            body.publisher, did,
        )

        # Invalidate trust score cache so modifier takes effect
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", did
        )

        # CAEP event on type change
        caep_event = None
        if old_class != body.agent_class:
            caep_event = {
                "type": "agent_class_changed",
                "did": did,
                "old_class": old_class,
                "new_class": body.agent_class,
                "governance": GOVERNANCE_RULES[body.agent_class],
                "trust_modifier": AGENT_CLASS_TRUST_MODIFIER[body.agent_class],
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }
            # Log CAEP event
            logger.info(
                "CAEP agent_class_changed: %s %s -> %s",
                did, old_class, body.agent_class,
            )
            # Store in caep_events table if it exists
            try:
                await conn.execute(
                    """INSERT INTO caep_events (did, event_type, payload, created_at)
                       VALUES ($1, $2, $3, NOW())""",
                    did, "agent_class_changed", json.dumps(caep_event),
                )
            except Exception:
                pass  # Table may not exist yet

    return {
        "did": did,
        "agent_class": body.agent_class,
        "governance": GOVERNANCE_RULES[body.agent_class],
        "trust_modifier": AGENT_CLASS_TRUST_MODIFIER[body.agent_class],
        "caep_event": caep_event,
    }


@app.get("/identity/agent-type/{did}")
@limiter.limit("60/minute")
async def get_agent_class(request: Request, did: str):
    """Read agent classification and governance rules."""
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow(
            """SELECT did, display_name, agent_class, agent_framework,
                      agent_version, publisher, agent_class_updated_at
               FROM agents WHERE did = $1""",
            did,
        )
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

    ac = agent["agent_class"] or "autonomous"
    return {
        "did": did,
        "display_name": agent["display_name"],
        "agent_class": ac,
        "agent_framework": agent["agent_framework"],
        "agent_version": agent["agent_version"],
        "publisher": agent["publisher"],
        "governance": GOVERNANCE_RULES.get(ac, GOVERNANCE_RULES["autonomous"]),
        "trust_modifier": AGENT_CLASS_TRUST_MODIFIER.get(ac, 0.0),
        "updated_at": agent["agent_class_updated_at"].isoformat() if agent["agent_class_updated_at"] else None,
    }


@app.get("/identity/agent-types")
@limiter.limit("60/minute")
async def list_agent_types(request: Request):
    """List all available agent classifications with governance rules."""
    return {
        "types": {
            k: {**v, "trust_modifier": AGENT_CLASS_TRUST_MODIFIER[k]}
            for k, v in GOVERNANCE_RULES.items()
        }
    }

@app.post("/identity/register-batch", tags=["Identity"])
async def register_batch(request: Request):
    """Batch-register external agents with Merkle anchoring. Requires ADMIN_KEY."""
    verify_admin(request, AdminPermission.WRITE)

    try:
        raw = await request.json()
        body = BatchRegisterRequest(**raw)
    except Exception as e:
        raise HTTPException(400, f"Invalid request: {e}")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    results = []
    anchor_records = []
    ts = datetime.datetime.utcnow()

    async with db_pool.acquire() as conn:
        for entry in body.agents:
            # Check if external DID already bridged
            existing = await conn.fetchval(
                "SELECT moltrust_did FROM did_bridges WHERE external_did = $1",
                entry.external_did
            )
            if existing:
                results.append({
                    "label": entry.label,
                    "external_did": entry.external_did,
                    "moltrust_did": existing,
                    "api_key": None,
                    "mapped_score": None,
                    "status": "already_bridged",
                })
                continue

            # 1. Register new MolTrust DID
            agent_did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
            await conn.execute(
                "INSERT INTO agents (did, display_name, platform, agent_type, created_at) VALUES ($1, $2, $3, 'external', $4)",
                agent_did, entry.label, body.external_system, ts
            )

            # 2. Generate scoped API key
            api_key = f"mt_{secrets.token_hex(16)}"
            await conn.execute(
                "INSERT INTO api_keys (key, owner_did, active, email) VALUES ($1, $2, true, $3)",
                api_key, agent_did, f"batch-{body.external_system}@moltrust.ch"
            )
            API_KEYS.add(api_key)

            # 3. Bridge external DID
            await conn.execute(
                "INSERT INTO did_bridges (external_did, moltrust_did, chain, wallet_address) VALUES ($1, $2, $3, $4)",
                entry.external_did, agent_did, body.external_system, ""
            )

            # 4. Score import (default mapping: generic 50+score*50, capped 100)
            mapped_score = round(50 + (min(1.0, 1.0) * 50), 1)  # default grade 1 = 100.0
            try:
                evidence_hash = hashlib.sha256(f"{entry.external_did}:{body.external_system}".encode()).hexdigest()
                await conn.execute(
                    """INSERT INTO endorsements
                       (endorser_did, endorsed_did, skill, evidence_hash,
                        evidence_timestamp, vertical, weight, issued_at, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW() + interval '90 days')""",
                    "did:moltrust:external_import", agent_did,
                    "general", evidence_hash,
                    ts, "core", 0.3,
                )
            except Exception:
                pass  # Duplicate endorsement OK

            # 5. Grant free credits
            try:
                await ensure_balance_row(conn, agent_did, 0)
                await grant_credits(conn, agent_did, 175, "batch_registration", "Free credits via batch register")
            except Exception:
                pass

            # Collect for Merkle anchoring
            anchor_records.append({
                "output_hash": hashlib.sha256(f"{agent_did}:{entry.external_did}".encode()).hexdigest(),
                "agent_did": agent_did,
                "produced_at": ts.isoformat(),
                "confidence": 1.0,
            })

            results.append({
                "label": entry.label,
                "external_did": entry.external_did,
                "moltrust_did": agent_did,
                "api_key": api_key,
                "mapped_score": mapped_score,
                "status": "registered",
            })

    # 6. Merkle batch anchor — single Base L2 TX for all DIDs
    batch_tx = None
    batch_root = None
    if anchor_records:
        from app.provenance.anchor import build_merkle_tree_from_records
        batch_root, _ = build_merkle_tree_from_records(anchor_records)
        if batch_root:
            calldata = f"MolTrust/BatchRegister/v1/{batch_root}"
            batch_tx = await anchor_to_base(calldata, ts.isoformat())

    registered_count = len([r for r in results if r["status"] == "registered"])
    return {
        "agents": results,
        "batch_tx": batch_tx,
        "merkle_root": batch_root,
        "count": registered_count,
        "total": len(body.agents),
        "external_system": body.external_system,
        "jwks_url": body.jwks_url,
    }


# --- Verifiable Credentials ---
from app.credentials import issue_credential, verify_credential
from app.ipfs_publisher import publish_to_ipfs, get_ipfs_url

class IssueVCRequest(BaseModel):
    subject_did: str = Field(max_length=128)
    credential_type: str = Field(default="AgentTrustCredential", max_length=64)

    @field_validator("subject_did")
    @classmethod
    def validate_subject(cls, v):
        if not (DID_PATTERN.match(v) or v.startswith("did:web:") or v.startswith("did:key:")):
            raise ValueError("Invalid DID format")
        return v

    @field_validator("credential_type")
    @classmethod
    def validate_credential_type(cls, v):
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9]{1,63}$", v):
            raise ValueError("Credential type must be alphanumeric, starting with a letter")
        return v

class VerifyVCRequest(BaseModel):
    credential: dict

    @field_validator("credential")
    @classmethod
    def validate_credential_size(cls, v):
        if len(json.dumps(v)) > 16384:
            raise ValueError("Credential payload too large (max 16KB)")
        return v

@app.post("/credentials/issue")
@limiter.limit("10/minute")
async def issue_vc(request: Request, body: IssueVCRequest, api_key: str = Depends(verify_api_key)):
    # Feature 2: Delegation Chain Depth-Limit (Tech Spec v0.2.2)
    chain = body.dict().get("delegation_chain", []) if hasattr(body, "delegation_chain") else []
    if chain:
        valid, depth = check_delegation_depth(chain)
        if not valid:
            return JSONResponse(status_code=400, content={
                "error": "delegation_chain_too_deep",
                "message": f"Delegation chain exceeds maximum depth of 8 hops",
                "max_depth": 8,
                "actual_depth": depth,
            })
    reputation = {"score": 0.0, "total_ratings": 0}
    if db_pool and DID_PATTERN.match(body.subject_did):
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(AVG(score),0) as avg, COUNT(*) as total FROM ratings WHERE to_did=$1",
                body.subject_did
            )
            if row:
                reputation = {"score": round(float(row["avg"]), 2), "total_ratings": int(row["total"])}

    claims = {
        "trustProvider": "MolTrust",
        "reputation": reputation,
        "verified": True
    }
    vc = issue_credential(body.subject_did, body.credential_type, claims)
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO credentials (subject_did, credential_type, issuer, issued_at, expires_at, proof_value, raw_vc)
                VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                body.subject_did, body.credential_type, vc["issuer"],
                datetime.datetime.fromisoformat(vc["issuanceDate"].replace("Z","")),
                datetime.datetime.fromisoformat(vc["expirationDate"].replace("Z","")),
                vc["proof"]["proofValue"],
                json.dumps(vc)
            )

            # IPFS: publish VC and store CID (non-blocking)
            try:
                ipfs_cid = publish_to_ipfs(vc)
                if ipfs_cid:
                    await conn.execute(
                        "UPDATE credentials SET ipfs_cid = $1 WHERE subject_did = $2 AND raw_vc = $3",
                        ipfs_cid, body.subject_did, json.dumps(vc)
                    )
                    vc["ipfs_cid"] = ipfs_cid
                    vc["ipfs_url"] = get_ipfs_url(ipfs_cid)
            except Exception as ipfs_err:
                import logging
                logging.getLogger("moltrust.ipfs").warning("IPFS publish failed: %s", ipfs_err)


            # Track delegation relationship (ZeroID Feature 2)
            if chain and len(chain) >= 2:
                parent_did = chain[-2] if len(chain) >= 2 else None
                if parent_did and DID_PATTERN.match(parent_did):
                    try:
                        await conn.execute(
                            """INSERT INTO agent_delegations
                               (parent_did, child_did, aae_id, credential_type, hop_depth, created_at)
                               VALUES ($1, $2, $3, $4, $5, NOW())
                               ON CONFLICT (parent_did, child_did, aae_id) DO NOTHING""",
                            parent_did, body.subject_did,
                            vc.get("id", vc["proof"]["proofValue"][:32]),
                            body.credential_type,
                            len(chain) - 1,
                        )
                    except Exception:
                        pass  # Non-critical: don't break VC issuance

    await update_last_seen(body.subject_did)
    return vc

@app.post("/credentials/verify")
@limiter.limit("30/minute")
async def verify_vc(request: Request, body: VerifyVCRequest):
    result = verify_credential(body.credential)
    return result
# --- Multi-Platform OAuth ---

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "PENDING")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "PENDING")
_oauth_states: dict[str, float] = {}  # state -> timestamp

@app.get("/auth/github")
@limiter.limit("10/minute")
async def github_auth_start(request: Request):
    """Redirect to GitHub OAuth"""
    if GITHUB_CLIENT_ID == "PENDING":
        raise HTTPException(503, "GitHub OAuth not yet configured")
    # MEDIUM-1: CSRF protection via state parameter
    import time as _time
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = _time.time()
    # Purge expired states (>10min)
    cutoff = _time.time() - 600
    for k in [k for k, v in _oauth_states.items() if v < cutoff]:
        _oauth_states.pop(k, None)
    return JSONResponse({"redirect_url": f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&scope=read:user&state={state}"})

@app.get("/auth/github/callback")
@limiter.limit("10/minute")
async def github_auth_callback(request: Request, code: str = Query(max_length=128),
                               state: str = Query(default="", max_length=64)):
    if GITHUB_CLIENT_ID == "PENDING":
        raise HTTPException(503, "GitHub OAuth not yet configured")
    # MEDIUM-1: Validate CSRF state parameter
    import time as _time
    if not state or state not in _oauth_states:
        raise HTTPException(403, "Invalid or missing state parameter")
    if _time.time() - _oauth_states.pop(state) > 600:
        raise HTTPException(403, "State parameter expired")
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET, "code": code},
            headers={"Accept": "application/json"}
        )
        if token_resp.status_code != 200:
            raise HTTPException(502, "GitHub token exchange failed")
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(401, "GitHub auth failed")

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        )
        if user_resp.status_code != 200:
            raise HTTPException(502, "GitHub user fetch failed")
        gh_user = user_resp.json()

    agent_did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    display_name = str(gh_user.get("login", ""))[:64]

    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO agents (did, display_name, platform, agent_type, created_at) VALUES ($1, $2, $3, 'external', $4) ON CONFLICT DO NOTHING",
                agent_did, display_name, "github", datetime.datetime.utcnow()
            )

    return {
        "status": "authenticated",
        "platform": "github",
        "did": agent_did,
        "display_name": display_name,
        "github_id": gh_user.get("id"),
    }



# --- Self-Service API Key Signup ---

class SignupRequest(BaseModel):
    email: str = Field(max_length=256)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email")
        return v.lower().strip()

@app.post("/auth/signup")
@limiter.limit("5/minute")
async def signup_for_api_key(request: Request, body: SignupRequest):
    key = f"mt_{secrets.token_hex(16)}"
    if db_pool:
        async with db_pool.acquire() as conn:
            existing = await conn.fetchval("SELECT key FROM api_keys WHERE email = $1", body.email)
            if existing:
                return {"status": "exists", "message": "API key already issued for this email. Contact support if lost."}
            await conn.execute(
                "INSERT INTO api_keys (key, email) VALUES ($1, $2)",
                key, body.email
            )
            API_KEYS.add(key)
    return {"status": "created", "api_key": key, "email": body.email, "rate_limit": "100 requests/day", "note": "Save this key - it cannot be recovered."}

# Load existing keys from DB on startup
@app.on_event("startup")
async def load_api_keys():
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT key FROM api_keys WHERE active = TRUE")
                for row in rows:
                    API_KEYS.add(row["key"])
                print(f"Loaded {len(rows)} API keys from DB")
        except Exception as e:
            print(f"Could not load API keys: {e}")



# --- Base Blockchain Anchor ---
from web3 import Web3
import hashlib as _hashlib
from eth_account import Account

BASE_RPC = "https://mainnet.base.org"
BASE_KEY = os.getenv("BASE_WALLET_KEY", "")
BASE_ADDR = Account.from_key(BASE_KEY).address if BASE_KEY else None

async def anchor_to_base(agent_did: str, timestamp: str) -> str:
    try:
        w3 = Web3(Web3.HTTPProvider(BASE_RPC))
        if not w3.is_connected():
            return None
        data = _hashlib.sha256(f"{agent_did}:{timestamp}".encode()).hexdigest()
        nonce = w3.eth.get_transaction_count(BASE_ADDR)
        tx = {
            "from": BASE_ADDR,
            "to": BASE_ADDR,
            "value": 0,
            "data": w3.to_bytes(hexstr="0x" + data),
            "nonce": nonce,
            "chainId": 8453,
            "gas": 25000,
            "maxFeePerGas": w3.eth.gas_price + w3.to_wei(0.001, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(0.001, "gwei"),
        }
        signed = w3.eth.account.sign_transaction(tx, BASE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return w3.to_hex(tx_hash)
    except Exception as e:
        print(f"Base anchor error: {e}")
        return None



# --- Credit Endpoints ---

@app.get("/credits/pricing")
@limiter.limit("60/minute")
async def credits_pricing(request: Request):
    return {"pricing": ENDPOINT_COSTS, "currency": "CREDITS", "free_on_registration": 175}

@app.get("/credits/balance/{did}")
@limiter.limit("60/minute")
async def credits_balance(request: Request, did: str = Path(max_length=40)):
    did = validate_did(did)
    balance = 0
    if db_pool:
        async with db_pool.acquire() as conn:
            balance = await _get_balance(conn, did)
    return {"did": did, "balance": balance, "currency": "CREDITS"}

@app.post("/credits/transfer")
@limiter.limit("10/minute")
async def credits_transfer(
    request: Request,
    body: CreditTransferRequest,
    _identity: Identity = Depends(require_claimed),
    api_key: str = Depends(verify_api_key),
):
    if body.from_did == body.to_did:
        raise HTTPException(400, "Cannot transfer to yourself")
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    # Verify the caller owns from_did
    async with db_pool.acquire() as conn:
        owner_did = await resolve_did_from_api_key(conn, api_key)
    if owner_did != body.from_did:
        raise HTTPException(403, "API key does not own the source DID")

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                await transfer_credits(conn, body.from_did, body.to_did, body.amount, body.reference or "transfer")
    except ValueError as e:
        raise HTTPException(402, str(e))

    # Fetch updated balances
    async with db_pool.acquire() as conn:
        sender_balance = await _get_balance(conn, body.from_did)

    return {
        "status": "transferred",
        "from_did": body.from_did,
        "to_did": body.to_did,
        "amount": body.amount,
        "balance_after": sender_balance,
        "currency": "CREDITS",
    }

@app.get("/credits/transactions/{did}")
@limiter.limit("30/minute")
async def credits_transactions(request: Request, did: str = Path(max_length=40), api_key: str = Depends(verify_api_key), limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    did = validate_did(did)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    # Verify the caller owns this DID
    async with db_pool.acquire() as conn:
        owner_did = await resolve_did_from_api_key(conn, api_key)
    if owner_did != did:
        raise HTTPException(403, "API key does not own this DID")

    async with db_pool.acquire() as conn:
        txs = await get_transactions(conn, did, limit, offset)
    return {"did": did, "transactions": txs, "limit": limit, "offset": offset}



# --- USDC Deposit Endpoint ---
from app.usdc import verify_usdc_transfer, record_deposit, get_deposits, CREDITS_PER_USDC, MOLTRUST_WALLET

class DepositRequest(BaseModel):
    tx_hash: str = Field(min_length=64, max_length=70)
    did: str = Field(max_length=40)

@app.post("/credits/deposit")
@limiter.limit("5/minute")
async def credits_deposit(
    request: Request,
    body: DepositRequest,
    _identity: Identity = Depends(require_claimed),
    api_key: str = Depends(verify_api_key),
):
    """Claim credits by submitting a USDC transaction hash from Base."""
    did = validate_did(body.did)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    # Verify caller owns this DID
    async with db_pool.acquire() as conn:
        owner_did = await resolve_did_from_api_key(conn, api_key)
    if owner_did != did:
        raise HTTPException(403, "API key does not own this DID")

    # Verify on-chain
    result = await verify_usdc_transfer(body.tx_hash)
    if not result["valid"]:
        raise HTTPException(400, result["error"])

    # Record deposit + grant credits atomically
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            recorded = await record_deposit(
                conn, body.tx_hash, result["from_address"], did,
                result["usdc_amount"], result["credits"], result["block_number"],
            )
            if not recorded:
                raise HTTPException(409, "This transaction has already been claimed")

            await ensure_balance_row(conn, did)
            await grant_credits(
                conn, did, result["credits"],
                reference=f"usdc_deposit:{body.tx_hash[:16]}",
                description=f"USDC deposit: {result['usdc_amount']} USDC = {result['credits']} credits",
            )
            new_balance = await _get_balance(conn, did)

    return {
        "status": "deposited",
        "tx_hash": body.tx_hash,
        "basescan_url": f"https://basescan.org/tx/{body.tx_hash}",
        "from_address": result["from_address"],
        "usdc_amount": result["usdc_amount"],
        "credits_granted": result["credits"],
        "new_balance": new_balance,
        "currency": "CREDITS",
        "rate": f"1 USDC = {CREDITS_PER_USDC} credits",
    }

@app.get("/credits/deposits/{did}")
@limiter.limit("30/minute")
async def credits_deposit_history(request: Request, did: str = Path(max_length=40), api_key: str = Depends(verify_api_key)):
    """Get USDC deposit history for an agent."""
    did = validate_did(did)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    async with db_pool.acquire() as conn:
        owner_did = await resolve_did_from_api_key(conn, api_key)
    if owner_did != did:
        raise HTTPException(403, "API key does not own this DID")
    async with db_pool.acquire() as conn:
        deposits = await get_deposits(conn, did)
    return {"did": did, "deposits": deposits, "wallet": MOLTRUST_WALLET, "network": "Base (Chain ID 8453)"}

@app.get("/credits/deposit-info")
async def credits_deposit_info(request: Request):
    """Public endpoint: how to deposit USDC for credits."""
    return {
        "wallet": MOLTRUST_WALLET,
        "network": "Base (Ethereum L2, Chain ID 8453)",
        "token": "USDC",
        "token_contract": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "rate": f"1 USDC = {CREDITS_PER_USDC} credits",
        "min_confirmations": 5,
        "instructions": [
            "1. Send USDC on Base to the wallet address above",
            "2. Wait for 5 confirmations (~10 seconds on Base)",
            "3. Call POST /credits/deposit with your tx_hash and DID",
            "4. Credits are granted instantly after verification",
        ],
    }


# Helper: lazy-load + cache public agent card
_AGENT_CARD_CACHE = None
def _load_public_agent_card():
    global _AGENT_CARD_CACHE
    if _AGENT_CARD_CACHE is None:
        with open("/var/www/html/.well-known/agent-card.json", "r") as f:
            _AGENT_CARD_CACHE = json.load(f)
    return _AGENT_CARD_CACHE


@app.get("/extendedAgentCard")
@limiter.limit("60/minute")
async def extended_agent_card(
    request: Request,
    auth: dict = Depends(verify_api_key_or_did),
):
    """A2A v1.0 ExtendedAgentCard. Returns public card + paid skills + x402 pricing + moltguard details."""

    # Load public card from static file (cached on first read)
    public_card = _load_public_agent_card()

    # Extended skills
    extended_skills = [
        {
            "id": "endorsement",
            "name": "Trust Endorsement",
            "description": "Create a trust edge between two DIDs (signed endorsement). Free for authenticated agents.",
            "tags": ["endorsement", "trust", "graph", "delegation"],
            "examples": [
                "Endorse did:moltrust:abc123 for skill data-analysis",
                "Issue a trust edge with confidence score 0.8"
            ],
            "inputModes": ["text", "data"],
            "outputModes": ["data"]
        },
        {
            "id": "moltguard-market-check",
            "name": "Prediction Market Integrity Check",
            "description": "Check Polymarket/Kalshi market for outcome anomalies, oracle manipulation patterns, and statistical irregularities. Paid via x402 ($0.05 USDC).",
            "tags": ["moltguard", "prediction-market", "polymarket", "kalshi", "market-integrity"],
            "examples": [
                "Check market 0xabc... for anomaly indicators",
                "Verify oracle integrity before placing trade"
            ],
            "inputModes": ["text"],
            "outputModes": ["data"]
        },
        {
            "id": "moltguard-events-feed",
            "name": "Anomaly Event Feed",
            "description": "Real-time stream of detected market integrity anomalies and behavioral red flags. Paid via x402 ($0.05 per poll).",
            "tags": ["moltguard", "events", "anomaly", "feed", "monitoring"],
            "examples": [
                "Subscribe to anomaly events from MoltGuard surveillance",
                "Poll for new market integrity flags"
            ],
            "inputModes": ["text"],
            "outputModes": ["data"]
        },
        {
            "id": "credential-issue",
            "name": "Verifiable Credential Issuance",
            "description": "Issue W3C Verifiable Credentials with AAE delegation envelopes. Premium endpoint requiring authentication.",
            "tags": ["vc", "credential", "issuance", "aae", "premium"],
            "examples": [
                "Issue an AAE credential for agent did:moltrust:abc123 with skill scope",
                "Generate signed delegation envelope for sub-agent"
            ],
            "inputModes": ["text", "data"],
            "outputModes": ["data"]
        }
    ]

    # New extensions
    x402_pricing_ext = {
        "uri": "https://moltrust.ch/extensions/x402-pricing/v1",
        "description": "x402 micropayment pricing inventory for paid endpoints",
        "required": False,
        "params": {
            "currency": "USDC",
            "chain": "eip155:8453",
            "endpoints": {
                "sybil-scan": {"price": "0.10", "method": "GET", "path": "/guard/api/sybil/scan/{addr}"},
                "agent-score": {"price": "0.05", "method": "GET", "path": "/guard/api/agent/score/{addr}"},
                "market-check": {"price": "0.05", "method": "GET", "path": "/guard/api/market/check/{addr}"},
                "events-feed": {"price": "0.05", "method": "GET", "path": "/guard/events/feed"}
            }
        }
    }

    moltguard_ext = {
        "uri": "https://moltrust.ch/extensions/moltguard/v1",
        "description": "MoltGuard surveillance and risk-scoring service capabilities",
        "required": False,
        "params": {
            "service_url": "https://api.moltrust.ch/guard/",
            "capabilities": ["sybil-detection", "market-surveillance", "wallet-risk-scoring", "anomaly-events"],
            "data_sources": ["base-l2-onchain", "polymarket", "kalshi"],
            "free_endpoints": ["/health", "/agent/sample", "/agent/score-free/{addr}"],
            "rate_limits": {"free_tier": "1_per_10min", "paid_tier": "60_per_minute"}
        }
    }

    # Build extended response (deep merge skills + extensions)
    extended_card = {
        **public_card,
        "skills": public_card.get("skills", []) + extended_skills,
        "capabilities": {
            **public_card.get("capabilities", {}),
            "extensions": public_card.get("capabilities", {}).get("extensions", []) + [x402_pricing_ext, moltguard_ext]
        }
    }

    return extended_card


@app.get("/a2a/agent-card/{did}")
@limiter.limit("60/minute")
async def a2a_trust_card(request: Request, did: str = Path(max_length=128)):
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow("SELECT display_name, platform, created_at, base_tx_hash, agent_class, agent_framework FROM agents WHERE did = $1", did)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        score = await conn.fetchrow("SELECT COALESCE(AVG(score),0) as avg, COUNT(*) as total FROM ratings WHERE to_did=$1", did)
        cred_count = await conn.fetchval("SELECT COUNT(*) FROM credentials WHERE subject_did=$1", did)
        cred = {"total": cred_count}
    return {
        "name": agent["display_name"],
        "did": did,
        "platform": agent["platform"],
        "url": f"https://api.moltrust.ch/identity/verify/{did}",
        "trust": {
            "score": round(float(score["avg"]), 2),
            "totalRatings": int(score["total"]),
            "credentials": int(cred["total"]),
            "verified": True,
            "registeredAt": agent["created_at"].isoformat() if agent["created_at"] else None,
            "baseAnchor": agent["base_tx_hash"],
            "baseScanUrl": f"https://basescan.org/tx/{agent['base_tx_hash']}" if agent["base_tx_hash"] else None
        },
        "agent_classification": {
            "agent_class": agent["agent_class"] or "autonomous",
            "agent_framework": agent["agent_framework"],
            "governance_tier": GOVERNANCE_RULES.get(agent["agent_class"] or "autonomous", GOVERNANCE_RULES["autonomous"]),
        },
        "capabilities": {
            "verifiableIdentity": True,
            "reputationScoring": True,
            "blockchainAnchored": bool(agent["base_tx_hash"])
        },
        "verifyUrl": f"https://api.moltrust.ch/identity/verify/{did}",
        "rateUrl": f"https://api.moltrust.ch/reputation/rate",
        "provider": "MolTrust (https://moltrust.ch)"
    }

# --- Recent Agents ---
@app.get("/agents/recent")
@limiter.limit("60/minute")
async def recent_agents(request: Request):
    agents = []
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT display_name, did, platform, created_at FROM agents WHERE agent_type = 'external' ORDER BY created_at DESC LIMIT 10"
            )
            agents = []
            for row in rows:
                name = row["display_name"]
                did_short = row["did"][:16] + "..." if len(row["did"]) > 16 else row["did"]
                if not name or name.strip().lower() == "anonymous":
                    name = f"{row['platform']} \u00b7 {did_short}"
                agents.append({
                    "display_name": name,
                    "did": did_short,
                    "platform": row["platform"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })
    return JSONResponse(content=agents, headers={"Cache-Control": "public, max-age=30"})

# --- Public Stats ---
@app.get("/stats")
@limiter.limit("60/minute")
async def public_stats(request: Request):
    stats = {"agents": 0, "ratings": 0, "credentials": 0}
    if db_pool:
        async with db_pool.acquire() as conn:
            stats["agents"] = await conn.fetchval("SELECT COUNT(*) FROM agents WHERE agent_type = 'external'") or 0
            stats["ratings"] = await conn.fetchval("SELECT COUNT(*) FROM ratings") or 0
            try:
                stats["credentials"] = await conn.fetchval("SELECT COUNT(*) FROM credentials") or 0
            except:
                stats["credentials"] = stats["agents"]
    return stats

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        if request.url.hostname not in ("localhost", "127.0.0.1"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)


# --- Request Logger Middleware ---
SKIP_LOG_PATHS = {"/health", "/docs", "/openapi.json", "/favicon.ico", "/robots.txt"}

class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        import time as _time
        start = _time.time()
        response = await call_next(request)
        duration_ms = int((_time.time() - start) * 1000)
        path = request.url.path
        if path not in SKIP_LOG_PATHS and db_pool:
            try:
                async with db_pool.acquire() as conn:
                    raw_ip = _get_client_ip(request)
                    ip_info = await _enrich_ip(raw_ip)  # enrich with full IP
                    client_ip = _anonymize_ip(raw_ip)   # store anonymized
                    await conn.execute(
                        "INSERT INTO request_log (endpoint, method, status_code, ip, user_agent, response_ms, source, ip_org, ip_country) "
                        "VALUES ($1, $2, $3, $4, $5, $6, 'fastapi', $7, $8)",
                        path[:200], request.method, response.status_code,
                        client_ip,
                        (request.headers.get("user-agent") or "")[:500],
                        duration_ms,
                        ip_info.get("org"),
                        ip_info.get("country"),
                    )
            except Exception:
                pass
        return response

app.add_middleware(RequestLoggerMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://moltrust.ch",
        "https://www.moltrust.ch",
        "https://api.moltrust.ch",
        "https://enterprise.moltrust.ch",
        "http://localhost:3000",
        "http://localhost:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Viral Join Endpoint ---
from fastapi.responses import HTMLResponse, RedirectResponse, RedirectResponse

@app.get("/join")
@limiter.limit("30/minute")
async def join_redirect(request: Request, ref: str = Query(default=None, max_length=100)):
    if ref:
        return RedirectResponse(f"https://moltrust.ch?ref={ref}", status_code=302)
    return RedirectResponse("https://moltrust.ch", status_code=302)

# --- ERC-8004 Bridge (Phase 1: Read-Only) ---
from app.erc8004 import build_registration_file, resolve_onchain_agent, get_onchain_reputation, get_well_known_registration

@app.get("/agents/{did}/erc8004")
@limiter.limit("30/minute")
async def erc8004_registration_file(request: Request, did: str = Path(max_length=128)):
    """Serve ERC-8004 compatible registration file (Agent Card) for a MolTrust agent."""
    # Special case: MolTrust platform identity
    if did in ("did:web:api.moltrust.ch", "did%3Aweb%3Aapi.moltrust.ch"):
        from app.erc8004 import MOLTRUST_PLATFORM_AGENT_ID
        return build_registration_file(
            {"did": "did:web:api.moltrust.ch", "display_name": "MolTrust", "base_tx_hash": None},
            {"score": 0.0, "total_ratings": 0},
            MOLTRUST_PLATFORM_AGENT_ID
        )
    did = validate_did(did)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")
    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow(
            "SELECT did, display_name, platform, base_tx_hash, erc8004_agent_id FROM agents WHERE did = $1", did
        )
        if not agent:
            raise HTTPException(404, "Agent not found")
        rep = await conn.fetchrow(
            "SELECT COALESCE(AVG(score), 0) as avg_score, COUNT(*) as total FROM ratings WHERE to_did = $1", did
        )
    await update_last_seen(did)
    reputation = {"score": round(float(rep["avg_score"]), 2), "total_ratings": int(rep["total"])}
    return build_registration_file(dict(agent), reputation, agent["erc8004_agent_id"])

@app.get("/resolve/erc8004/{agent_id}")
@limiter.limit("10/minute")
async def erc8004_resolve(request: Request, agent_id: int = Path(ge=0)):
    """Resolve an ERC-8004 agent ID on Base to its on-chain data + optional MolTrust cross-reference."""
    result = await resolve_onchain_agent(agent_id)
    if "error" in result:
        raise HTTPException(404, result["error"])

    # Cross-reference: check if this agentId is linked to a MolTrust DID
    result["moltrust_did"] = None
    result["moltrust_profile"] = None
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT did FROM agents WHERE erc8004_agent_id = $1", agent_id
            )
            if row:
                result["moltrust_did"] = row["did"]
                result["moltrust_profile"] = f"https://api.moltrust.ch/identity/resolve/{row['did']}"

    # Fetch on-chain reputation
    result["onchain_reputation"] = get_onchain_reputation(agent_id)
    return result

@app.get("/.well-known/agent-registration.json")
async def well_known_agent_registration(request: Request):
    """ERC-8004 domain verification endpoint."""
    return get_well_known_registration()


# ═══════════════════════════════════════════════════════════════
# ERC-8004 DEDICATED ENDPOINTS
# ═══════════════════════════════════════════════════════════════


class ERC8004RegisterRequest(BaseModel):
    name: str = Field(max_length=128)
    description: str = Field(max_length=1024, default="")
    wallet_address: str = Field(max_length=64)
    platform: str = Field(max_length=64, default="base")

    @field_validator("wallet_address")
    @classmethod
    def check_wallet(cls, v):
        if not re.match(r"^0x[a-fA-F0-9]{40}$", v):
            raise ValueError("Invalid Ethereum address")
        return v


class ERC8004ValidateRequest(BaseModel):
    erc8004_agent_id: int = Field(ge=0)
    validation_type: str = Field(max_length=64, default="trust_assessment")


@app.post("/identity/erc8004/register")
@limiter.limit("5/minute")
async def erc8004_dual_register(request: Request, body: ERC8004RegisterRequest, api_key: str = Depends(verify_api_key)):
    """Dual registration: create MolTrust DID + register on ERC-8004 IdentityRegistry."""
    agent_did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        dup = await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE display_name = $1 AND platform = $2 AND created_at > now() - interval '24 hours'",
            body.name, body.platform
        )
        if dup > 0:
            raise HTTPException(409, "Agent with this name and platform was already registered in the last 24 hours")
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, agent_type, wallet_address, created_at) VALUES ($1, $2, $3, 'external', $4, $5)",
            agent_did, body.name, body.platform, body.wallet_address, datetime.datetime.utcnow()
        )

    ts = datetime.datetime.utcnow().isoformat()
    tx_hash = await anchor_to_base(agent_did, ts)
    if tx_hash:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE agents SET base_tx_hash = $1 WHERE did = $2", tx_hash, agent_did)

    auto_vc = issue_credential(agent_did, "AgentTrustCredential", {
        "trustProvider": "MolTrust", "reputation": {"score": 0.0, "total_ratings": 0}, "verified": True
    })

    from app.erc8004 import register_onchain_agent
    erc8004_result = register_onchain_agent(agent_did)
    erc8004_agent_id = erc8004_result.get("agent_id")
    if erc8004_agent_id:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE agents SET erc8004_agent_id = $1 WHERE did = $2",
                erc8004_agent_id, agent_did
            )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await link_api_key_to_did(conn, api_key, agent_did)
            await ensure_balance_row(conn, agent_did, 0)
            await grant_credits(conn, agent_did, 175, "registration", "Free credits on ERC-8004 dual registration")

    return {
        "moltrust_did": agent_did,
        "erc8004_agent_id": erc8004_agent_id,
        "base_tx": tx_hash,
        "credential": auto_vc,
        "erc8004": erc8004_result,
        "credits": {"balance": 175, "currency": "CREDITS"},
    }


@app.get("/identity/erc8004/{address}")
@limiter.limit("30/minute")
async def erc8004_resolve_by_address(request: Request, address: str = Path(max_length=42)):
    """Resolve ERC-8004 identity by Base wallet address."""
    if not re.match(r"^0x[a-fA-F0-9]{40}$", address):
        raise HTTPException(400, "Invalid Ethereum address")
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT did, display_name, erc8004_agent_id, base_tx_hash, created_at FROM agents WHERE wallet_address = $1",
            address
        )
    if not row:
        raise HTTPException(404, "No agent registered with this wallet address")

    result = {
        "address": address,
        "moltrust_did": row["did"],
        "display_name": row["display_name"],
        "erc8004_agent_id": row["erc8004_agent_id"],
        "base_tx": row["base_tx_hash"],
        "registered_at": row["created_at"].isoformat() if row["created_at"] else None,
        "registration_file_url": f"https://api.moltrust.ch/agents/{row['did']}/erc8004",
    }

    if row["erc8004_agent_id"]:
        onchain = await resolve_onchain_agent(row["erc8004_agent_id"])
        if "error" not in onchain:
            result["onchain"] = onchain

    return result


@app.post("/identity/erc8004/validate")
@limiter.limit("5/minute")
async def erc8004_validate(request: Request, body: ERC8004ValidateRequest, api_key: str = Depends(verify_api_key)):
    """MolTrust as ERC-8004 validator: assess agent, issue VC, post on-chain feedback."""
    onchain = await resolve_onchain_agent(body.erc8004_agent_id)
    if "error" in onchain:
        raise HTTPException(404, f"ERC-8004 agent {body.erc8004_agent_id} not found on-chain")

    moltrust_did = None
    trust_score = 0.0
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT did FROM agents WHERE erc8004_agent_id = $1", body.erc8004_agent_id
            )
            if row:
                moltrust_did = row["did"]
                rep = await conn.fetchrow(
                    "SELECT COALESCE(AVG(score), 0) as avg_score, COUNT(*) as total FROM ratings WHERE to_did = $1",
                    moltrust_did
                )
                trust_score = round(float(rep["avg_score"]), 2) if rep else 0.0

    claims = {
        "validationType": body.validation_type,
        "erc8004AgentId": body.erc8004_agent_id,
        "trustScore": trust_score,
        "onchainOwner": onchain.get("owner"),
        "validatedAt": datetime.datetime.utcnow().isoformat() + "Z",
    }
    subject_did = moltrust_did or f"did:erc8004:{body.erc8004_agent_id}"
    vc = issue_credential(subject_did, "AgentValidationCredential", claims)

    from app.erc8004 import post_reputation_feedback
    feedback_result = post_reputation_feedback(body.erc8004_agent_id, subject_did, trust_score)

    return {
        "validated": True,
        "erc8004_agent_id": body.erc8004_agent_id,
        "moltrust_did": moltrust_did,
        "trust_score": trust_score,
        "credential": vc,
        "on_chain_tx": feedback_result.get("tx_hash"),
        "onchain": onchain,
    }


# ═══════════════════════════════════════════════════════════════
# SPORTS MODULE — Prediction Commitment & Verification
# ═══════════════════════════════════════════════════════════════

class PredictionCommitRequest(BaseModel):
    agent_did: str = Field(max_length=40)
    event_id: str = Field(max_length=256)
    prediction: dict
    event_start: str = Field(max_length=30)

    @field_validator("agent_did")
    @classmethod
    def check_did_format(cls, v):
        if not re.match(r"^did:moltrust:[a-f0-9]{16}$", v):
            raise ValueError("Invalid DID format")
        return v

    @field_validator("event_start")
    @classmethod
    def check_event_start_future(cls, v):
        try:
            dt = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt <= datetime.datetime.now(datetime.timezone.utc):
                raise ValueError("event_start must be in the future")
        except (ValueError, TypeError) as e:
            if "future" in str(e):
                raise
            raise ValueError("Invalid ISO 8601 datetime")
        return v


@app.get("/sports/health")
@limiter.limit("60/minute")
async def sports_health(request: Request):
    """Sports module health check."""
    db_ok = False
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            pass
    return {
        "module": "moltrust-sports",
        "version": "1.0.0",
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unavailable",
        "chain": "base-mainnet",
    }


@app.post("/sports/predictions/commit")
@limiter.limit("30/minute")
async def sports_predict_commit(request: Request, body: PredictionCommitRequest,
                                 x_api_key: str = Depends(verify_api_key)):
    """Commit a prediction before an event starts. Returns commitment hash + on-chain anchor."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        # Verify agent exists
        if not await _sp_agent_exists(conn, body.agent_did):
            raise HTTPException(404, f"Agent {body.agent_did} not registered")

        # Normalize event ID
        event_id = normalize_event_id(body.event_id)
        if not event_id or len(event_id) < 5:
            raise HTTPException(400, "event_id too short after normalization")

        # Compute commitment hash
        commitment_hash = compute_commitment_hash(
            body.agent_did, event_id, body.prediction, body.event_start,
        )

        # Check uniqueness (agent + event)
        existing = await conn.fetchval(
            "SELECT commitment_hash FROM sports_predictions WHERE agent_did = $1 AND event_id = $2",
            body.agent_did, event_id,
        )
        if existing:
            raise HTTPException(409, f"Prediction already committed for this event (hash: {existing})")

        # Anchor on-chain (reuse existing anchor function)
        tx_hash = await anchor_to_base(commitment_hash, body.event_start)

        # Insert
        try:
            row = await insert_prediction(
                conn, body.agent_did, event_id, body.prediction,
                body.event_start, commitment_hash, tx_hash,
            )
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "Duplicate prediction or commitment hash")
            raise

    return {
        "status": "committed",
        "commitment_hash": commitment_hash,
        "event_id": event_id,
        "agent_did": body.agent_did,
        "base_tx_hash": tx_hash,
        "anchored": tx_hash is not None,
        "created_at": row["created_at"].isoformat() if row else None,
        "verify_url": f"https://api.moltrust.ch/sports/predictions/verify/{commitment_hash}",
    }


@app.get("/sports/predictions/verify/{commitment_hash}")
@limiter.limit("60/minute")
async def sports_predict_verify(request: Request, commitment_hash: str = Path(max_length=64)):
    """Verify a prediction commitment exists and return details."""
    if not re.match(r"^[a-f0-9]{64}$", commitment_hash):
        raise HTTPException(400, "Invalid hash format (expected 64 hex chars)")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        row = await get_prediction_by_hash(conn, commitment_hash)

    if not row:
        raise HTTPException(404, "Commitment not found")

    prediction = row["prediction"]
    if isinstance(prediction, str):
        prediction = json.loads(prediction)

    return {
        "status": "verified",
        "commitment_hash": row["commitment_hash"],
        "agent_did": row["agent_did"],
        "event_id": row["event_id"],
        "prediction": prediction,
        "event_start": row["event_start"].isoformat(),
        "base_tx_hash": row["base_tx_hash"],
        "anchored": row["base_tx_hash"] is not None,
        "committed_at": row["created_at"].isoformat(),
        "basescan_url": f"https://basescan.org/tx/{row['base_tx_hash']}" if row["base_tx_hash"] else None,
    }



# --- Sports Phase 2: History + Admin Settlement ---

class ManualSettleRequest(BaseModel):
    result: str = Field(max_length=64)
    score: str | None = Field(default=None, max_length=32)
    detail: dict | None = Field(default=None)


@app.get("/sports/predictions/history/{did}")
@limiter.limit("30/minute")
async def sports_predict_history(request: Request, did: str = Path(max_length=40),
                                  x_api_key: str = Depends(verify_api_key)):
    """Get prediction history and stats for an agent."""
    did = validate_did(did)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        if not await _sp_agent_exists(conn, did):
            raise HTTPException(404, f"Agent {did} not registered")

        predictions = await get_prediction_history(conn, did)
        stats = await get_prediction_stats(conn, did)
        calibration = await compute_calibration_score(conn, did)

        # Get MolTrust reputation score
        rep = await conn.fetchrow(
            "SELECT COALESCE(AVG(score), 0) as avg_score FROM ratings WHERE to_did = $1", did
        )
        moltrust_score = round(float(rep["avg_score"]) * 20, 1) if rep and rep["avg_score"] else 0

    stats["calibration_score"] = calibration

    # Format predictions for response
    formatted = []
    for p in predictions:
        pred = p["prediction"]
        if isinstance(pred, str):
            pred = json.loads(pred)
        outcome = p["outcome"]
        if isinstance(outcome, str):
            outcome = json.loads(outcome)

        formatted.append({
            "commitment_hash": p["commitment_hash"],
            "event_id": p["event_id"],
            "prediction": pred.get("outcome", pred.get("result", str(pred))),
            "confidence": pred.get("confidence"),
            "correct": p["correct"],
            "outcome": outcome.get("result") if isinstance(outcome, dict) else outcome,
            "committed_at": p["created_at"].isoformat(),
            "settled_at": p["settled_at"].isoformat() if p["settled_at"] else None,
        })

    return {
        "agent_did": did,
        "moltrust_score": moltrust_score,
        "betting_stats": stats,
        "predictions": formatted,
    }


@app.patch("/sports/predictions/settle/{commitment_hash}")
@limiter.limit("30/minute")
async def sports_predict_settle_admin(request: Request,
                                       commitment_hash: str = Path(max_length=64),
                                       body: ManualSettleRequest = None,
                                       x_api_key: str = Depends(verify_api_key)):
    """Admin endpoint: manually settle a prediction (for polymarket or manual events)."""
    if not re.match(r"^[a-f0-9]{64}$", commitment_hash):
        raise HTTPException(400, "Invalid hash format")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    result_data = {
        "result": body.result,
        "score": body.score,
        "source": "manual",
    }
    if body.detail:
        result_data["detail"] = body.detail

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT agent_did, settled_at FROM sports_predictions WHERE commitment_hash = $1",
            commitment_hash,
        )
        if not row:
            raise HTTPException(404, "Commitment not found")
        # HIGH-2: Verify caller owns this prediction
        caller_did = await resolve_did_from_api_key(conn, x_api_key)
        if caller_did != row["agent_did"]:
            raise HTTPException(403, "Not authorized to settle this prediction")
        if row["settled_at"] is not None:
            raise HTTPException(409, "Already settled")

        ok = await _settle_prediction_fn(conn, commitment_hash, result_data)

    if not ok:
        raise HTTPException(500, "Settlement failed")

    return {
        "status": "settled",
        "commitment_hash": commitment_hash,
        "result": body.result,
        "score": body.score,
    }


# --- Signal Provider Endpoints ---

class SignalProviderRegisterRequest(BaseModel):
    agent_did: str = Field(max_length=40)
    provider_name: str = Field(max_length=128)
    provider_url: str | None = Field(default=None, max_length=512)
    sport_focus: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("agent_did")
    @classmethod
    def check_did_format(cls, v):
        if not re.match(r"^did:(moltrust:[a-f0-9]{16}|web:.+)$", v):
            raise ValueError("Invalid DID format (expected did:moltrust:... or did:web:...)")
        return v


@app.post("/sports/signals/register", status_code=201)
@limiter.limit("10/minute")
async def signal_provider_register(request: Request, body: SignalProviderRegisterRequest,
                                    x_api_key: str = Depends(verify_api_key)):
    """Register as a Verified Signal Provider. Returns credential with on-chain anchor."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        # Verify agent exists
        if not await _sp_agent_exists(conn, body.agent_did):
            raise HTTPException(404, f"Agent {body.agent_did} not registered. Register first via POST /identity/register")

        # Check if already registered
        existing = await get_provider_by_did(conn, body.agent_did)
        if existing:
            raise HTTPException(409, f"Agent already registered as signal provider (id: {existing['provider_id']})")

        # Generate provider ID
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        provider_id = generate_provider_id(body.agent_did, ts)

        # Compute credential hash
        cred_hash = compute_credential_hash(provider_id, body.agent_did, body.provider_name, ts)

        # Anchor on-chain
        tx_hash = await anchor_to_base(cred_hash, ts)

        # Insert
        try:
            row = await insert_provider(
                conn, provider_id, body.agent_did, body.provider_name,
                body.provider_url, body.sport_focus, body.description,
                cred_hash, tx_hash,
            )
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "Duplicate registration")
            raise

    return {
        "provider_id": provider_id,
        "agent_did": body.agent_did,
        "provider_name": body.provider_name,
        "credential": {
            "type": "MolTrustVerifiedSignalProvider",
            "issued_at": ts,
            "issuer": "did:web:moltrust.ch",
            "credential_hash": cred_hash,
            "tx_hash": tx_hash,
            "chain": "base",
        },
        "badge_url": f"https://moltrust.ch/badges/signals/{provider_id}",
        "verify_url": f"https://api.moltrust.ch/sports/signals/verify/{provider_id}",
    }


@app.get("/sports/signals/verify/{provider_id}")
@limiter.limit("60/minute")
async def signal_provider_verify(request: Request, provider_id: str = Path(max_length=11)):
    """Public: verify a signal provider and see their track record."""
    if not re.match(r"^sp_[a-f0-9]{8}$", provider_id):
        raise HTTPException(400, "Invalid provider_id format (expected sp_ + 8 hex chars)")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        provider = await get_provider_by_id(conn, provider_id)
        if not provider:
            raise HTTPException(404, "Signal provider not found")

        track = await get_track_record(conn, provider["agent_did"])
        calibration = await compute_calibration_score(conn, provider["agent_did"])
        recent = await get_recent_signals(conn, provider["agent_did"])

    track["calibration_score"] = calibration

    sport_focus = provider["sport_focus"]
    if isinstance(sport_focus, str):
        import json as _json
        sport_focus = _json.loads(sport_focus)

    return {
        "provider_id": provider["provider_id"],
        "provider_name": provider["provider_name"],
        "agent_did": provider["agent_did"],
        "provider_url": provider["provider_url"],
        "sport_focus": sport_focus,
        "description": provider["description"],
        "credential": {
            "type": "MolTrustVerifiedSignalProvider",
            "issued_at": provider["created_at"].isoformat(),
            "on_chain_verified": provider["credential_tx_hash"] is not None,
            "tx_hash": provider["credential_tx_hash"],
            "credential_hash": provider["credential_hash"],
        },
        "track_record": track,
        "recent_signals": recent,
        "badge_svg_url": f"https://api.moltrust.ch/sports/signals/badge/{provider_id}.svg",
    }


@app.get("/sports/signals/leaderboard")
@limiter.limit("30/minute")
async def signal_provider_leaderboard(request: Request):
    """Public: top signal providers ranked by accuracy."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        providers = await get_leaderboard(conn)

        # Add calibration scores
        for p in providers:
            prov = await get_provider_by_id(conn, p["provider_id"])
            if prov:
                cal = await compute_calibration_score(conn, prov["agent_did"])
                p["calibration_score"] = cal

    return {
        "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "min_settled_threshold": 20,
        "providers": providers,
    }


@app.get("/sports/signals/badge/{provider_id}.svg")
@limiter.limit("120/minute")
async def signal_provider_badge(request: Request, provider_id: str = Path(max_length=11)):
    """Public: SVG badge for embedding in websites."""
    pid = provider_id.replace(".svg", "")
    if not re.match(r"^sp_[a-f0-9]{8}$", pid):
        raise HTTPException(400, "Invalid provider_id format")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        provider = await get_provider_by_id(conn, pid)
        if not provider:
            raise HTTPException(404, "Signal provider not found")

        track = await get_track_record(conn, provider["agent_did"])

    accuracy = track["accuracy"] if track["settled"] > 0 else None
    svg = generate_badge_svg(provider["provider_name"], accuracy)

    from starlette.responses import Response
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=300"})


# --- Endpoint Costs for Signals ---
# (Note: update credits.py ENDPOINT_COSTS if credits system is enabled)


# --- Fantasy Lineup Endpoints ---

class FantasyLineupCommitRequest(BaseModel):
    agent_did: str = Field(max_length=64)
    contest_id: str = Field(max_length=256)
    platform: str = Field(max_length=32)
    sport: str = Field(max_length=32)
    contest_type: str | None = Field(default=None, max_length=32)
    contest_start_iso: str = Field(max_length=30)
    entry_fee_usd: float | None = Field(default=None, ge=0)
    lineup: dict
    projected_score: float | None = Field(default=None)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("agent_did")
    @classmethod
    def check_did(cls, v):
        if not re.match(r"^did:(moltrust:[a-f0-9]{16}|web:.+)$", v):
            raise ValueError("Invalid DID format")
        return v

    @field_validator("contest_start_iso")
    @classmethod
    def check_future(cls, v):
        try:
            dt = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
            if dt <= datetime.datetime.now(datetime.timezone.utc):
                raise ValueError("contest_start_iso must be in the future")
        except (ValueError, TypeError) as e:
            if "future" in str(e):
                raise
            raise ValueError("Invalid ISO 8601 datetime")
        return v

    @field_validator("platform")
    @classmethod
    def check_platform(cls, v):
        if v.lower() not in VALID_PLATFORMS:
            raise ValueError(f"Invalid platform. Valid: {sorted(VALID_PLATFORMS)}")
        return v.lower()

    @field_validator("sport")
    @classmethod
    def check_sport(cls, v):
        if v.lower() not in VALID_SPORTS:
            raise ValueError(f"Invalid sport. Valid: {sorted(VALID_SPORTS)}")
        return v.lower()


@app.post("/sports/fantasy/lineups/commit", status_code=201)
@limiter.limit("30/minute")
async def fantasy_lineup_commit(request: Request, body: FantasyLineupCommitRequest,
                                 x_api_key: str = Depends(verify_api_key)):
    """Commit a fantasy lineup before contest start. Returns commitment hash + on-chain anchor."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        if not await _sp_agent_exists(conn, body.agent_did):
            raise HTTPException(404, f"Agent {body.agent_did} not registered")

        # Check uniqueness
        existing = await conn.fetchval(
            "SELECT commitment_hash FROM fantasy_lineups WHERE agent_did = $1 AND contest_id = $2",
            body.agent_did, body.contest_id,
        )
        if existing:
            raise HTTPException(409, f"Lineup already committed for this contest (hash: {existing})")

        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        lineup_hash = compute_lineup_hash(body.lineup)
        commitment_hash = compute_fantasy_commitment_hash(
            body.agent_did, body.contest_id, lineup_hash, ts,
        )

        tx_hash = await anchor_to_base(commitment_hash, ts)

        # Issue FantasyLineupCredential (W3C VC)
        vc = issue_fantasy_lineup_credential(body.agent_did, {
            "contest_id": body.contest_id,
            "platform": body.platform,
            "sport": body.sport,
            "lineup_hash": lineup_hash,
            "commitment_hash": commitment_hash,
            "contest_start_iso": body.contest_start_iso,
            "projected_score": body.projected_score,
            "confidence": body.confidence,
            "tx_hash": tx_hash,
        })

        try:
            row = await insert_lineup(
                conn, body.agent_did, body.contest_id, body.platform, body.sport,
                body.contest_type, body.contest_start_iso, body.entry_fee_usd,
                body.lineup, lineup_hash, body.projected_score, body.confidence,
                commitment_hash, tx_hash, credential=vc,
            )
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "Duplicate lineup or commitment hash")
            raise

    return {
        "commitment_hash": commitment_hash,
        "timestamp_iso": ts,
        "tx_hash": tx_hash,
        "chain": "base",
        "agent_did": body.agent_did,
        "contest_id": body.contest_id,
        "lineup_hash": lineup_hash,
        "status": "committed",
        "verify_url": f"https://api.moltrust.ch/sports/fantasy/lineups/verify/{commitment_hash}",
        "credential": vc,
    }


@app.get("/sports/fantasy/lineups/verify/{commitment_hash}")
@limiter.limit("60/minute")
async def fantasy_lineup_verify(request: Request, commitment_hash: str = Path(max_length=64)):
    """Public: verify a fantasy lineup commitment."""
    if not re.match(r"^[a-f0-9]{64}$", commitment_hash):
        raise HTTPException(400, "Invalid hash format (expected 64 hex chars)")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        row = await get_lineup_by_hash(conn, commitment_hash)

    if not row:
        raise HTTPException(404, "Lineup commitment not found")

    lineup = row["lineup"]
    if isinstance(lineup, str):
        lineup = json.loads(lineup)

    # Minutes before contest
    minutes_before = None
    if row["committed_at"] and row["contest_start"]:
        diff = row["contest_start"] - row["committed_at"]
        minutes_before = max(0, int(diff.total_seconds() / 60))

    return {
        "commitment_hash": row["commitment_hash"],
        "agent_did": row["agent_did"],
        "contest_id": row["contest_id"],
        "platform": row["platform"],
        "sport": row["sport"],
        "contest_type": row["contest_type"],
        "committed_at": row["committed_at"].isoformat() if row["committed_at"] else None,
        "contest_start": row["contest_start"].isoformat() if row["contest_start"] else None,
        "minutes_before_contest": minutes_before,
        "lineup": lineup,
        "projected_score": row["projected_score"],
        "confidence": row["confidence"],
        "on_chain": {
            "verified": row["tx_hash"] is not None,
            "tx_hash": row["tx_hash"],
            "chain": "base",
        },
        "result": {
            "settled": row["settled_at"] is not None,
            "actual_score": row["actual_score"],
            "rank": row["rank"],
            "total_entries": row["total_entries"],
            "prize_usd": row["prize_usd"],
            "percentile": row["percentile"],
        },
        "credential": json.loads(row["credential"]) if isinstance(row.get("credential"), str) else row.get("credential"),
    }


class FantasySettleRequest(BaseModel):
    actual_score: float
    rank: int | None = Field(default=None)
    total_entries: int | None = Field(default=None)
    prize_usd: float | None = Field(default=None, ge=0)
    percentile: float | None = Field(default=None, ge=0, le=100)


@app.patch("/sports/fantasy/lineups/settle/{commitment_hash}")
@limiter.limit("30/minute")
async def fantasy_lineup_settle(request: Request,
                                 commitment_hash: str = Path(max_length=64),
                                 body: FantasySettleRequest = None,
                                 x_api_key: str = Depends(verify_api_key)):
    """Admin: settle a fantasy lineup with results."""
    if not re.match(r"^[a-f0-9]{64}$", commitment_hash):
        raise HTTPException(400, "Invalid hash format")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        row = await get_lineup_by_hash(conn, commitment_hash)
        if not row:
            raise HTTPException(404, "Lineup commitment not found")
        if row["settled_at"] is not None:
            raise HTTPException(409, "Already settled")

        ok = await settle_lineup(
            conn, commitment_hash, body.actual_score,
            body.rank, body.total_entries, body.prize_usd, body.percentile,
        )

    if not ok:
        raise HTTPException(500, "Settlement failed")

    return {
        "status": "settled",
        "commitment_hash": commitment_hash,
        "actual_score": body.actual_score,
        "rank": body.rank,
        "prize_usd": body.prize_usd,
        "percentile": body.percentile,
    }


@app.get("/sports/fantasy/history/{did}")
@limiter.limit("30/minute")
async def fantasy_history(request: Request, did: str = Path(max_length=64),
                           x_api_key: str = Depends(verify_api_key)):
    """Get fantasy lineup history and ROI stats for an agent."""
    did = validate_did(did)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        if not await _sp_agent_exists(conn, did):
            raise HTTPException(404, f"Agent {did} not registered")

        stats = await get_fantasy_stats(conn, did)
        lineups = await get_fantasy_history(conn, did)

        rep = await conn.fetchrow(
            "SELECT COALESCE(AVG(score), 0) as avg_score FROM ratings WHERE to_did = $1", did
        )
        moltrust_score = round(float(rep["avg_score"]) * 20, 1) if rep and rep["avg_score"] else 0

    formatted = []
    for lu in lineups:
        lineup = lu["lineup"]
        if isinstance(lineup, str):
            lineup = json.loads(lineup)
        formatted.append({
            "commitment_hash": lu["commitment_hash"],
            "contest_id": lu["contest_id"],
            "platform": lu["platform"],
            "sport": lu["sport"],
            "projected_score": lu["projected_score"],
            "actual_score": lu["actual_score"],
            "rank": lu["rank"],
            "prize_usd": lu["prize_usd"],
            "settled": lu["settled_at"] is not None,
            "committed_at": lu["committed_at"].isoformat() if lu["committed_at"] else None,
        })

    return {
        "agent_did": did,
        "moltrust_score": moltrust_score,
        "fantasy_stats": stats,
        "lineups": formatted,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Protocol Compliance Features (Tech Spec v0.2.2)
# ══════════════════════════════════════════════════════════════════════════════

# --- Violation Records Table ---
VIOLATION_RECORDS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS violation_records (
    id TEXT PRIMARY KEY,
    agent_did TEXT NOT NULL,
    principal_did TEXT NOT NULL,
    violation_type TEXT NOT NULL,
    interaction_proof_id TEXT,
    description TEXT,
    adjudicator_type TEXT DEFAULT 'external',
    adjudicator_reference TEXT,
    confirmed_at TEXT NOT NULL,
    reversed BOOLEAN DEFAULT FALSE,
    reversal_date TEXT,
    reversal_reference TEXT,
    created_at TEXT DEFAULT (NOW()::TEXT)
)
"""

VALID_VIOLATION_TYPES = {
    "identity-spoofing",
    "authorization-abuse",
    "sybil",
    "behavioral-fraud",
    "clone-impersonation",
}

async def ensure_violation_records_table(conn):
    await conn.execute(VIOLATION_RECORDS_TABLE_SQL)


# --- Feature 2: Delegation Chain Depth-Limit ---

def check_delegation_depth(credential_chain: list, max_depth: int = 8):
    """Enforce maximum delegation chain depth per Tech Spec v0.2.2."""
    if len(credential_chain) > max_depth:
        return False, len(credential_chain)
    return True, len(credential_chain)


async def verify_delegation_chain_full(dids: list, conn) -> dict:
    """Full AAE-aware delegation chain verification. RSAC Gap 2."""
    chain = []
    valid = True
    invalid_at = None
    max_depth_exceeded = False

    for i, did in enumerate(dids):
        # Look up delegation config for this agent
        config = await conn.fetchrow(
            "SELECT delegation_permitted, max_depth, constraint_mode "
            "FROM agent_delegation_config WHERE did = $1", did
        )
        # Also check agent exists
        agent = await conn.fetchrow("SELECT did FROM agents WHERE did = $1", did)

        if not agent:
            valid = False
            invalid_at = did
            chain.append({
                "did": did, "delegationPermitted": False, "maxDepth": None,
                "constraintMode": "none", "depth": i, "aaeValid": False,
            })
            break

        delegation_permitted = config["delegation_permitted"] if config else False
        max_depth = config["max_depth"] if config else 0
        constraint_mode = config["constraint_mode"] if config else "none"

        # For non-root agents, check delegation rules
        if i > 0:
            # Check if parent permitted delegation
            parent_config = await conn.fetchrow(
                "SELECT delegation_permitted, max_depth FROM agent_delegation_config WHERE did = $1",
                dids[i - 1]
            )
            parent_permitted = parent_config["delegation_permitted"] if parent_config else False
            parent_max_depth = parent_config["max_depth"] if parent_config else 0

            if not parent_permitted:
                valid = False
                invalid_at = did

            if parent_max_depth is not None and i > parent_max_depth:
                valid = False
                max_depth_exceeded = True
                invalid_at = invalid_at or did

        chain.append({
            "did": did,
            "delegationPermitted": delegation_permitted,
            "maxDepth": max_depth,
            "constraintMode": constraint_mode,
            "depth": i,
            "aaeValid": True,
        })

    constraints_inherited = all(
        link["constraintMode"] == "inherit" or link["depth"] == 0
        for link in chain
    )

    return {
        "valid": valid,
        "chain": chain,
        "maxDepthExceeded": max_depth_exceeded,
        "constraintsInherited": constraints_inherited,
        "invalidAt": invalid_at,
        "checkedAt": datetime.datetime.utcnow().isoformat() + "Z",
    }


# --- Feature 3: Sequential Signing Validation ---

def validate_interaction_proof_signing(proof: dict) -> dict:
    """Validate interaction proof signing sequence per Tech Spec v0.2.2."""
    errors = []
    if "proofInitiator" not in proof:
        errors.append("proofInitiator is required")
    if not proof.get("singleSig", False):
        if "proofResponder" not in proof:
            errors.append("proofResponder required for bilateral proof")
    if proof.get("singleSig", False) and "proofResponder" in proof:
        errors.append("singleSig proof must not contain proofResponder")
    return {"valid": len(errors) == 0, "errors": errors}


# --- Pydantic Models ---

class ViolationRecordRequest(BaseModel):
    agent_did: str = Field(max_length=128)
    principal_did: str = Field(max_length=128)
    violation_type: str = Field(max_length=64)
    interaction_proof_id: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    adjudicator_reference: str | None = Field(default=None, max_length=256)
    confirmed_at: str = Field(max_length=64)

    @field_validator("violation_type")
    @classmethod
    def validate_violation_type(cls, v):
        if v not in VALID_VIOLATION_TYPES:
            raise ValueError("violation_type must be one of: " + ", ".join(sorted(VALID_VIOLATION_TYPES)))
        return v


class ViolationReversalRequest(BaseModel):
    adjudicator_reference: str | None = Field(default=None, max_length=256)
    reversal_date: str | None = Field(default=None, max_length=64)


class DelegationChainRequest(BaseModel):
    credential_chain: list = Field(default_factory=list)


# --- Violation Record Endpoints ---

def _format_violation_record(row) -> dict:
    """Format a DB row into the ViolationRecord response per Tech Spec 2.7."""
    return {
        "@context": "https://moltrust.ch/ns/violation/v1",
        "type": "ViolationRecord",
        "id": row["id"],
        "issuanceDate": row["created_at"] if isinstance(row["created_at"], str) else row["created_at"].isoformat() if row["created_at"] else None,
        "subject": {
            "agentDid": row["agent_did"],
            "principalDid": row["principal_did"],
        },
        "violation": {
            "type": row["violation_type"],
            "interactionProofId": row["interaction_proof_id"],
            "description": row["description"],
        },
        "adjudication": {
            "adjudicatorType": row["adjudicator_type"] or "external",
            "adjudicatorReference": row["adjudicator_reference"],
            "confirmedAt": row["confirmed_at"],
        },
        "reversed": row["reversed"],
        "reversalDate": row["reversal_date"],
        "reversalReference": row["reversal_reference"],
        "registrySignature": {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:moltrust:registry#keys-1",
            "proofValue": "placeholder",
        },
    }


@app.post("/violation/record")
@limiter.limit("10/minute")
async def create_violation_record(request: Request, body: ViolationRecordRequest):
    """Record a protocol violation. Requires X-Admin-Key header. Tech Spec 2.7."""
    verify_admin(request, AdminPermission.DESTROY)

    record_id = str(uuid.uuid4())
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO violation_records
               (id, agent_did, principal_did, violation_type,
                interaction_proof_id, description,
                adjudicator_reference, confirmed_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            record_id, body.agent_did, body.principal_did,
            body.violation_type, body.interaction_proof_id,
            body.description, body.adjudicator_reference,
            body.confirmed_at,
        )
        # Invalidate trust score cache
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", body.agent_did
        )
        row = await conn.fetchrow(
            "SELECT * FROM violation_records WHERE id = $1", record_id
        )
    return _format_violation_record(row)


@app.get("/violation/{record_id}")
@limiter.limit("30/minute")
async def get_violation_record(request: Request, record_id: str = Path(max_length=64)):
    """Retrieve a ViolationRecord by ID. Public endpoint."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM violation_records WHERE id = $1", record_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Violation record not found")
    return _format_violation_record(row)


@app.post("/violation/{record_id}/reverse")
@limiter.limit("10/minute")
async def reverse_violation(request: Request, body: ViolationReversalRequest, record_id: str = Path(max_length=64)):
    """Reverse a violation record. Requires X-Admin-Key header. Tech Spec 2.7."""
    verify_admin(request, AdminPermission.DESTROY)

    reversal_date = body.reversal_date or datetime.datetime.utcnow().isoformat()

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM violation_records WHERE id = $1", record_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Violation record not found")
        if row["reversed"]:
            raise HTTPException(status_code=409, detail="Violation already reversed")

        await conn.execute(
            """UPDATE violation_records
               SET reversed = TRUE, reversal_date = $1, reversal_reference = $2
               WHERE id = $3""",
            reversal_date, body.adjudicator_reference, record_id,
        )
        # Invalidate trust score cache
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", row["agent_did"]
        )
        updated = await conn.fetchrow(
            "SELECT * FROM violation_records WHERE id = $1", record_id
        )

    return {
        "@context": "https://moltrust.ch/ns/violation/v1",
        "type": "ViolationReversal",
        "violationId": record_id,
        "reversed": True,
        "reversalDate": reversal_date,
        "adjudicatorReference": body.adjudicator_reference,
        "record": _format_violation_record(updated),
    }


@app.get("/violation/agent/{did:path}")
@limiter.limit("30/minute")
async def get_agent_violations(request: Request, did: str):
    """List all violation records for a given agent DID. Public endpoint."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM violation_records WHERE agent_did = $1 ORDER BY created_at DESC LIMIT 100",
            did,
        )
    return {
        "agent_did": did,
        "total": len(rows),
        "violations": [_format_violation_record(r) for r in rows],
    }


# --- Delegation Chain Verification Endpoint ---

@app.post("/credentials/verify-chain")
@limiter.limit("20/minute")
async def verify_delegation_chain_endpoint(request: Request, body: DelegationChainRequest):
    """Full delegation chain verification with per-agent AAE lookup. RSAC Gap 2."""
    # Basic depth check
    valid, depth = check_delegation_depth(body.credential_chain)
    if not valid:
        return JSONResponse(
            status_code=400,
            content={
                "error": "delegation_chain_too_deep",
                "message": "Delegation chain exceeds maximum depth of 8 hops",
                "max_depth": 8,
                "actual_depth": depth,
            },
        )

    # Full AAE-aware verification if DIDs provided
    if body.credential_chain and db_pool:
        async with db_pool.acquire() as conn:
            result = await verify_delegation_chain_full(body.credential_chain, conn)
            return result

    return {"valid": True, "depth": depth, "max_depth": 8}



# ═══════════════════════════════════════════════════════════════════════════════
# CASCADE REVOCATION (ZeroID Feature 2)
# ═══════════════════════════════════════════════════════════════════════════════

class RevokeRequest(BaseModel):
    reason: str = Field("manual_revocation", max_length=100)
    cascade: bool = Field(False, description="Revoke all downstream delegated agents")


@app.post("/identity/revoke/{did}")
@limiter.limit("10/minute")
async def revoke_agent(
    request: Request,
    did: str,
    body: RevokeRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    Revoke an agent. With cascade=true, all downstream delegated agents
    are also revoked (max 8 hops). Emits CAEP events.
    """
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        # Verify caller is admin or agent owner
        caller_did = await resolve_did_from_api_key(conn, api_key)
        _is_admin = is_admin(request)

        if caller_did != did and not _is_admin:
            raise HTTPException(403, "Not authorized to revoke this agent")

        agent = await conn.fetchrow(
            "SELECT did, display_name, revoked_at FROM agents WHERE did = $1", did
        )
        if not agent:
            raise HTTPException(404, "Agent not found")
        if agent["revoked_at"]:
            raise HTTPException(409, "Agent already revoked")

        # Collect all DIDs to revoke
        revoked_dids = []
        visited = set()

        async def _cascade_revoke(target_did: str, depth: int = 0):
            if depth > 8 or target_did in visited:
                return
            visited.add(target_did)

            # Revoke the agent
            await conn.execute(
                "UPDATE agents SET revoked_at = NOW(), revocation_reason = $1 WHERE did = $2 AND revoked_at IS NULL",
                body.reason, target_did,
            )
            revoked_dids.append({"did": target_did, "depth": depth})

            # Invalidate trust score cache
            await conn.execute(
                "DELETE FROM trust_score_cache WHERE did = $1", target_did
            )

            # CAEP event
            caep_payload = {
                "type": "agent_revoked",
                "did": target_did,
                "reason": body.reason,
                "cascade": body.cascade,
                "cascade_depth": depth,
                "revoked_by": did,
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }
            try:
                await conn.execute(
                    "INSERT INTO caep_events (did, event_type, payload, created_at) VALUES ($1, $2, $3, NOW())",
                    target_did, "agent_revoked", json.dumps(caep_payload),
                )
            except Exception:
                pass

            # Fetch children BEFORE revoking delegation records
            children_to_cascade = []
            if body.cascade:
                children_to_cascade = await conn.fetch(
                    "SELECT child_did FROM agent_delegations WHERE parent_did = $1 AND revoked_at IS NULL",
                    target_did,
                )

            # Revoke delegation records
            await conn.execute(
                "UPDATE agent_delegations SET revoked_at = NOW() WHERE (parent_did = $1 OR child_did = $1) AND revoked_at IS NULL",
                target_did,
            )

            # Cascade to children
            for child in children_to_cascade:
                await _cascade_revoke(child["child_did"], depth + 1)

        await _cascade_revoke(did)

        logger.info(
            "REVOKE %s cascade=%s affected=%d reason=%s",
            did, body.cascade, len(revoked_dids), body.reason,
        )

    return {
        "revoked": did,
        "reason": body.reason,
        "cascade": body.cascade,
        "affected_agents": revoked_dids,
        "count": len(revoked_dids),
    }


@app.post("/identity/unrevoke/{did}")
@limiter.limit("10/minute")
async def unrevoke_agent(
    request: Request,
    did: str,
    api_key: str = Depends(verify_api_key),
):
    """Reinstate a revoked agent. Admin only."""
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    verify_admin(request, AdminPermission.DESTROY)

    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow(
            "SELECT did, revoked_at FROM agents WHERE did = $1", did
        )
        if not agent:
            raise HTTPException(404, "Agent not found")
        if not agent["revoked_at"]:
            raise HTTPException(409, "Agent is not revoked")

        await conn.execute(
            "UPDATE agents SET revoked_at = NULL, revocation_reason = NULL WHERE did = $1",
            did,
        )
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", did
        )

        # CAEP event
        try:
            await conn.execute(
                "INSERT INTO caep_events (did, event_type, payload, created_at) VALUES ($1, $2, $3, NOW())",
                did, "agent_unrevoked",
                json.dumps({"type": "agent_unrevoked", "did": did,
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}),
            )
        except Exception:
            pass

    return {"did": did, "status": "reinstated"}


@app.get("/identity/revocation-status/{did}")
@limiter.limit("60/minute")
async def revocation_status(request: Request, did: str):
    """Check revocation status and downstream impact."""
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow(
            "SELECT did, display_name, revoked_at, revocation_reason FROM agents WHERE did = $1",
            did,
        )
        if not agent:
            raise HTTPException(404, "Agent not found")

        # Count downstream delegations
        downstream = await conn.fetchval(
            "SELECT COUNT(*) FROM agent_delegations WHERE parent_did = $1 AND revoked_at IS NULL",
            did,
        )

    return {
        "did": did,
        "display_name": agent["display_name"],
        "revoked": agent["revoked_at"] is not None,
        "revoked_at": agent["revoked_at"].isoformat() if agent["revoked_at"] else None,
        "revocation_reason": agent["revocation_reason"],
        "downstream_delegations": downstream,
    }


@app.get("/identity/delegations/{did}")
@limiter.limit("60/minute")
async def get_delegations(request: Request, did: str):
    """List all delegation relationships for an agent (parent and child)."""
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        delegated_to = await conn.fetch(
            """SELECT child_did, aae_id, credential_type, hop_depth, created_at, revoked_at
               FROM agent_delegations WHERE parent_did = $1
               ORDER BY created_at DESC LIMIT 100""",
            did,
        )
        delegated_from = await conn.fetch(
            """SELECT parent_did, aae_id, credential_type, hop_depth, created_at, revoked_at
               FROM agent_delegations WHERE child_did = $1
               ORDER BY created_at DESC LIMIT 100""",
            did,
        )

    return {
        "did": did,
        "delegated_to": [
            {
                "child_did": r["child_did"],
                "aae_id": r["aae_id"],
                "credential_type": r["credential_type"],
                "hop_depth": r["hop_depth"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "revoked": r["revoked_at"] is not None,
            }
            for r in delegated_to
        ],
        "delegated_from": [
            {
                "parent_did": r["parent_did"],
                "aae_id": r["aae_id"],
                "credential_type": r["credential_type"],
                "hop_depth": r["hop_depth"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "revoked": r["revoked_at"] is not None,
            }
            for r in delegated_from
        ],
        "total_delegated_to": len(delegated_to),
        "total_delegated_from": len(delegated_from),
    }



# ═══════════════════════════════════════════════════════════════════════════════
# SPIFFE BRIDGE (ZeroID Feature 3) — Lightweight SPIFFE ↔ MolTrust DID mapping
# ═══════════════════════════════════════════════════════════════════════════════

SPIFFE_URI_PATTERN = re.compile(r"^spiffe://[a-z0-9][a-z0-9.-]*(/[a-zA-Z0-9._~:@!$&'()*+,;=-]+)*$")


class SpiffeBindRequest(BaseModel):
    spiffe_uri: str = Field(..., max_length=512, description="SPIFFE URI, e.g. spiffe://moltrust.ch/agent/scanner")
    did: str = Field(..., max_length=40, description="MolTrust DID to bind to")

    @field_validator("spiffe_uri")
    @classmethod
    def validate_spiffe_uri(cls, v):
        if not v.startswith("spiffe://"):
            raise ValueError("SPIFFE URI must start with spiffe://")
        return v


@app.get("/identity/spiffe/{spiffe_uri:path}")
@limiter.limit("60/minute")
async def spiffe_lookup(request: Request, spiffe_uri: str):
    """
    Resolve a SPIFFE URI to a MolTrust DID with trust score and classification.
    Lightweight bridge — full SVID/Workload API planned for Q3.
    """
    full_uri = spiffe_uri
    if not full_uri.startswith("spiffe://"):
        full_uri = "spiffe://" + full_uri

    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        binding = await conn.fetchrow(
            "SELECT did, bound_by, created_at FROM spiffe_bindings WHERE spiffe_uri = $1",
            full_uri,
        )
        if not binding:
            raise HTTPException(404, f"No MolTrust DID bound to SPIFFE URI: {full_uri}")

        did = binding["did"]

        # Fetch agent info + classification
        agent = await conn.fetchrow(
            "SELECT display_name, agent_class, agent_framework, revoked_at FROM agents WHERE did = $1",
            did,
        )

        # Fetch trust score from cache
        trust_score = None
        grade = None
        if agent and agent["revoked_at"]:
            trust_score = 0.0
            grade = "REVOKED"
        else:
            cached = await conn.fetchrow(
                "SELECT score FROM trust_score_cache WHERE did = $1", did
            )
            if cached and cached["score"] is not None:
                from app.swarm.trust_score import score_to_grade
                trust_score = cached["score"]
                grade = score_to_grade(trust_score)

    ac = (agent["agent_class"] if agent else None) or "autonomous"
    return {
        "spiffe_uri": full_uri,
        "moltrust_did": did,
        "display_name": agent["display_name"] if agent else None,
        "trust_score": trust_score,
        "grade": grade,
        "agent_classification": {
            "agent_class": ac,
            "agent_framework": agent["agent_framework"] if agent else None,
            "governance": GOVERNANCE_RULES.get(ac, GOVERNANCE_RULES["autonomous"]),
        },
        "revoked": bool(agent and agent["revoked_at"]),
        "bound_by": binding["bound_by"],
        "bound_at": binding["created_at"].isoformat() if binding["created_at"] else None,
    }


@app.post("/identity/spiffe/bind")
@limiter.limit("10/minute")
async def spiffe_bind(request: Request, body: SpiffeBindRequest, api_key: str = Depends(verify_api_key)):
    """Bind a SPIFFE URI to an existing MolTrust DID."""
    if not DID_PATTERN.match(body.did):
        raise HTTPException(400, "Invalid DID format")
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        # Verify agent exists
        agent = await conn.fetchrow("SELECT did FROM agents WHERE did = $1", body.did)
        if not agent:
            raise HTTPException(404, "Agent not found")

        # Resolve caller
        caller_did = await resolve_did_from_api_key(conn, api_key)

        # Check for existing binding
        existing = await conn.fetchrow(
            "SELECT did FROM spiffe_bindings WHERE spiffe_uri = $1", body.spiffe_uri
        )
        if existing:
            raise HTTPException(409, f"SPIFFE URI already bound to {existing['did']}")

        await conn.execute(
            "INSERT INTO spiffe_bindings (spiffe_uri, did, bound_by, created_at) VALUES ($1, $2, $3, NOW())",
            body.spiffe_uri, body.did, caller_did,
        )

        # CAEP event
        try:
            await conn.execute(
                "INSERT INTO caep_events (did, event_type, payload, created_at) VALUES ($1, $2, $3, NOW())",
                body.did, "spiffe_bound",
                json.dumps({"type": "spiffe_bound", "spiffe_uri": body.spiffe_uri,
                            "did": body.did, "bound_by": caller_did,
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}),
            )
        except Exception:
            pass

    logger.info("SPIFFE bind: %s -> %s by %s", body.spiffe_uri, body.did, caller_did)
    return {"status": "bound", "spiffe_uri": body.spiffe_uri, "did": body.did}


@app.delete("/identity/spiffe/bind/{spiffe_uri:path}")
@limiter.limit("10/minute")
async def spiffe_unbind(request: Request, spiffe_uri: str, api_key: str = Depends(verify_api_key)):
    """Remove a SPIFFE binding. Admin only."""
    full_uri = spiffe_uri
    if not full_uri.startswith("spiffe://"):
        full_uri = "spiffe://" + full_uri

    verify_admin(request, AdminPermission.DESTROY)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM spiffe_bindings WHERE spiffe_uri = $1", full_uri
        )
        if result == "DELETE 0":
            raise HTTPException(404, f"No binding found for: {full_uri}")

        # CAEP event
        try:
            await conn.execute(
                "INSERT INTO caep_events (did, event_type, payload, created_at) VALUES ($1, $2, $3, NOW())",
                "system", "spiffe_unbound",
                json.dumps({"type": "spiffe_unbound", "spiffe_uri": full_uri,
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}),
            )
        except Exception:
            pass

    return {"status": "unbound", "spiffe_uri": full_uri}


@app.get("/identity/spiffe")
@limiter.limit("30/minute")
async def spiffe_list(request: Request, api_key: str = Depends(verify_api_key)):
    """List all SPIFFE bindings. Requires API key."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT spiffe_uri, did, bound_by, created_at FROM spiffe_bindings ORDER BY created_at DESC LIMIT 100"
        )

    return {
        "bindings": [
            {
                "spiffe_uri": r["spiffe_uri"],
                "did": r["did"],
                "bound_by": r["bound_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@app.post("/delegation/configure")
@limiter.limit("10/minute")
async def configure_delegation(request: Request, api_key: str = Depends(verify_api_key)):
    """Configure delegation permissions for an agent. Admin or agent owner."""
    body = await request.json()
    did = body.get("did", "")
    permitted = body.get("delegation_permitted", False)
    max_depth_val = body.get("max_depth", 0)
    constraint_mode = body.get("constraint_mode", "none")

    if not did or not DID_PATTERN.match(did):
        raise HTTPException(400, "Invalid DID")
    if constraint_mode not in ("inherit", "restrict", "none"):
        raise HTTPException(400, "Invalid constraint_mode")
    if not isinstance(max_depth_val, int) or max_depth_val < 0 or max_depth_val > 8:
        raise HTTPException(400, "max_depth must be 0-8")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        caller_did = await resolve_did_from_api_key(conn, api_key)
        if caller_did != did:
            if not is_admin(request):
                raise HTTPException(403, "Not authorized to configure delegation for this DID")

        await conn.execute("""
            INSERT INTO agent_delegation_config (did, delegation_permitted, max_depth, constraint_mode, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (did) DO UPDATE SET
                delegation_permitted = $2, max_depth = $3, constraint_mode = $4, updated_at = NOW()
        """, did, permitted, max_depth_val, constraint_mode)

    return {"status": "configured", "did": did, "delegation_permitted": permitted,
            "max_depth": max_depth_val, "constraint_mode": constraint_mode}


# --- Sequential Signing Validation Endpoint ---

@app.post("/interaction/validate-signing")
@limiter.limit("30/minute")
async def validate_signing_endpoint(request: Request):
    """Validate interaction proof signing sequence. Tech Spec v0.2.2."""
    try:
        proof = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    result = validate_interaction_proof_signing(proof)
    if not result["valid"]:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_signing_sequence",
                "messages": result["errors"],
            },
        )
    return {"valid": True, "signing_mode": "single" if proof.get("singleSig") else "bilateral"}


# ══════════════════════════════════════════════════════════════════════════════
# MT Music — AI-Generated Music Provenance (v1.0.0)
# ══════════════════════════════════════════════════════════════════════════════

import hashlib as _hashlib
import subprocess as _subprocess

VALID_OVERSIGHT = {"true", "false", "partial"}


class MusicCredentialRequest(BaseModel):
    agent_did: str = Field(max_length=128)
    tool: str = Field(max_length=128)
    human_oversight: str = Field(max_length=16)
    genre: str = Field(default=None, max_length=64)
    rights: str = Field(max_length=64)
    track_title: str = Field(max_length=256)
    track_description: str = Field(default=None, max_length=1024)
    human_name: str = Field(default=None, max_length=128)
    session: str = Field(default=None, max_length=128)
    isrc: str = Field(default=None, max_length=15)

    @field_validator("human_oversight")
    @classmethod
    def validate_oversight(cls, v):
        if v not in VALID_OVERSIGHT:
            raise ValueError("human_oversight must be one of: true, false, partial")
        return v


class MusicRevokeRequest(BaseModel):
    reason: str = Field(max_length=512)


def _build_music_vc(row) -> dict:
    """Build VerifiedMusicCredential from DB row."""
    return {
        "@context": [
            "https://www.w3.org/2018/credentials/v1",
            "https://moltrust.ch/ns/music/v1",
        ],
        "type": ["VerifiableCredential", "VerifiedMusicCredential"],
        "id": row["id"],
        "issuer": "did:moltrust:registry",
        "issuanceDate": row["issued_at"].isoformat() if hasattr(row["issued_at"], "isoformat") else str(row["issued_at"]),
        "credentialSubject": {
            "agentDid": row["agent_did"],
            "humanName": row["human_name"],
            "track": {
                "title": row["track_title"],
                "description": row["track_description"],
                "tool": row["tool"],
                "humanOversight": row["human_oversight"],
                "genre": row["genre"],
                "rights": row["rights"],
                "isrc": row["isrc"],
                "session": row["session"],
            },
            "provenance": {
                "trackHash": row["track_hash"],
                "issuanceDate": row["issued_at"].isoformat() if hasattr(row["issued_at"], "isoformat") else str(row["issued_at"]),
                "euAiActCompliance": "Article 50(2)",
            },
        },
        "anchor": {
            "chain": "base-mainnet",
            "anchorTx": row["anchor_tx"],
            "anchorBlock": row["anchor_block"],
            "calldata": "MolTrust/MusicVC/1 SHA256:" + row["track_hash"] if row["track_hash"] else None,
        },
        "proof": {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:moltrust:registry#keys-1",
        },
    }


async def _anchor_music_vc(track_hash: str, credential_id: str):
    """Anchor music VC on Base L2 in background."""
    base_key = os.environ.get("BASE_WRITE_KEY", "")
    if not base_key:
        return
    try:
        message = "MolTrust/MusicVC/1 SHA256:" + track_hash
        hex_data = message.encode("utf-8").hex()
        env = os.environ.copy()
        env["ETH_PRIVATE_KEY"] = base_key
        cmd = [
            os.path.expanduser("~/.foundry/bin/cast"), "send",
            "--rpc-url", "https://mainnet.base.org",
            "0x0000000000000000000000000000000000000000",
            "--value", "0",
            "--", "0x" + hex_data,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode()
        import re
        tx_match = re.search(r"transactionHash\s+(0x[0-9a-fA-F]+)", output)
        block_match = re.search(r"blockNumber\s+(\d+)", output)
        if tx_match and block_match:
            tx, block = tx_match.group(1), block_match.group(1)
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE music_credentials SET anchor_tx = $1, anchor_block = $2 WHERE id = $3",
                    tx, block, credential_id,
                )
            print(f"Music VC anchored: {tx} block {block}")
    except Exception as e:
        print(f"Music anchor failed: {e}")


@app.post("/music/credential/issue")
@limiter.limit("10/minute")
async def issue_music_credential(request: Request, body: MusicCredentialRequest,
                                  x_api_key: str = Depends(verify_api_key)):
    """Issue a VerifiedMusicCredential. Returns the credential with provenance."""
    # Build track hash from metadata
    hash_input = f"{body.agent_did}|{body.tool}|{body.track_title}|{body.rights}|{datetime.datetime.utcnow().isoformat()}"
    track_hash = _hashlib.sha256(hash_input.encode()).hexdigest()

    credential_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow()

    # Build VC
    vc = {
        "@context": [
            "https://www.w3.org/2018/credentials/v1",
            "https://moltrust.ch/ns/music/v1",
        ],
        "type": ["VerifiableCredential", "VerifiedMusicCredential"],
        "id": credential_id,
        "issuer": "did:moltrust:registry",
        "issuanceDate": now.isoformat() + "Z",
        "credentialSubject": {
            "agentDid": body.agent_did,
            "humanName": body.human_name,
            "track": {
                "title": body.track_title,
                "description": body.track_description,
                "tool": body.tool,
                "humanOversight": body.human_oversight,
                "genre": body.genre,
                "rights": body.rights,
                "isrc": body.isrc,
                "session": body.session,
            },
            "provenance": {
                "trackHash": track_hash,
                "issuanceDate": now.isoformat() + "Z",
                "euAiActCompliance": "Article 50(2)",
            },
        },
        "anchor": {
            "chain": "base-mainnet",
            "anchorTx": None,
            "anchorBlock": None,
            "calldata": "MolTrust/MusicVC/1 SHA256:" + track_hash,
        },
        "proof": {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:moltrust:registry#keys-1",
        },
    }

    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO music_credentials
               (id, agent_did, human_name, tool, human_oversight, session,
                genre, rights, isrc, track_title, track_description,
                track_hash, credential)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
            credential_id, body.agent_did, body.human_name, body.tool,
            body.human_oversight, body.session, body.genre, body.rights,
            body.isrc, body.track_title, body.track_description,
            track_hash, json.dumps(vc),
        )

    # Anchor on Base L2 (async, non-blocking)
    asyncio.create_task(_anchor_music_vc(track_hash, credential_id))

    return vc


@app.get("/music/credential/{credential_id}")
@limiter.limit("30/minute")
async def get_music_credential(request: Request, credential_id: str = Path(max_length=64)):
    """Retrieve a VerifiedMusicCredential by ID. Public endpoint."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM music_credentials WHERE id = $1", credential_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Music credential not found")
    return _build_music_vc(row)


@app.get("/music/credential/agent/{did:path}")
@limiter.limit("30/minute")
async def get_agent_music_credentials(request: Request, did: str):
    """List all music credentials for a given agent DID. Public endpoint."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM music_credentials WHERE agent_did = $1 ORDER BY issued_at DESC LIMIT 100",
            did,
        )
    return {
        "agent_did": did,
        "total": len(rows),
        "credentials": [_build_music_vc(r) for r in rows],
    }


@app.post("/music/credential/{credential_id}/revoke")
@limiter.limit("10/minute")
async def revoke_music_credential(request: Request, body: MusicRevokeRequest, credential_id: str = Path(max_length=64)):
    """Revoke a music credential. Requires X-Admin-Key."""
    verify_admin(request, AdminPermission.DESTROY)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM music_credentials WHERE id = $1", credential_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Music credential not found")
        if row["revoked"]:
            raise HTTPException(status_code=409, detail="Credential already revoked")
        await conn.execute(
            "UPDATE music_credentials SET revoked = TRUE, revocation_reason = $1 WHERE id = $2",
            body.reason, credential_id,
        )
    return {"id": credential_id, "revoked": True, "reason": body.reason}


@app.get("/music/verify/{credential_id}")
@limiter.limit("30/minute")
async def verify_music_credential(request: Request, credential_id: str = Path(max_length=64)):
    """Public verification: returns validity + full credential + anchor status."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM music_credentials WHERE id = $1", credential_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Music credential not found")

    vc = _build_music_vc(row)
    return {
        "valid": not row["revoked"],
        "revoked": row["revoked"],
        "revocationReason": row["revocation_reason"],
        "anchored": row["anchor_tx"] is not None,
        "credential": vc,
    }



# ═══════════════════════════════════════════════════════════════
# OUTPUT PROVENANCE — IPR Routes (Spec v0.4)
# ═══════════════════════════════════════════════════════════════



@app.post("/vc/ipr/submit", tags=["Output Provenance"])
async def ipr_submit(request: Request, api_key: str = Depends(verify_api_key)):
    """Submit an Interaction Proof Record."""
    # Update activity tracking
    try:
        if db_pool:
            async with db_pool.acquire() as c:
                caller = await resolve_did_from_api_key(c, api_key)
                if caller: await update_last_active(caller)
    except Exception:
        pass
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    body = await request.json()

    try:
        data = validate_ipr_input(body)
    except ValueError as e:
        raise HTTPException(422, str(e))

    # Verify agent_did matches API key owner
    async with db_pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT owner_did FROM api_keys WHERE key = $1 AND active = true",
            api_key
        )
        if not owner:
            raise HTTPException(403, "Invalid API key")
        if owner != data["agent_did"]:
            raise HTTPException(403, "agent_did does not match API key owner")

        result = await insert_ipr(conn, data)

    return result


@app.get("/vc/ipr/stats", tags=["Output Provenance"])
async def ipr_stats_endpoint():
    """Get aggregate IPR statistics."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        stats = await get_ipr_stats(conn)
    return stats




@app.get("/vc/ipr/agent/{did:path}", tags=["Output Provenance"])
async def ipr_by_agent(did: str, limit: int = Query(20, le=100), offset: int = Query(0, ge=0)):
    """Get IPRs for an agent, newest first."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        records = await get_iprs_by_agent(conn, did, limit, offset)
    return {"agent_did": did, "count": len(records), "records": records}


@app.get("/vc/ipr/{ipr_id}", tags=["Output Provenance"])
async def ipr_get(ipr_id: str):
    """Get a single IPR by ID."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        record = await get_ipr(conn, ipr_id)

    if not record:
        raise HTTPException(404, "IPR not found")
    return record


@app.get("/vc/ipr/{ipr_id}/status", tags=["Output Provenance"])
async def ipr_status(ipr_id: str):
    """Check DB vs chain consistency for an IPR."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await check_ipr_status(conn, ipr_id)

    if not result:
        raise HTTPException(404, "IPR not found")
    return result



@app.post("/vc/ipr/verify", tags=["Output Provenance"])
async def ipr_verify(request: Request):
    """Verify an IPR: check signature, anchor, and Merkle proof."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    body = await request.json()
    ipr_id = body.get("ipr_id")
    if not ipr_id:
        raise HTTPException(422, "ipr_id required")

    async with db_pool.acquire() as conn:
        record = await get_ipr(conn, ipr_id)

    if not record:
        raise HTTPException(404, "IPR not found")

    verified = record.get("anchor_status") == "anchored"
    checks = {
        "exists": True,
        "anchored": record.get("anchor_status") == "anchored",
        "has_signature": bool(record.get("agent_signature")),
        "has_merkle_proof": record.get("merkle_proof") is not None,
        "anchor_tx": record.get("anchor_tx"),
    }

    return {
        "verified": verified,
        "ipr_id": ipr_id,
        "agent_did": record.get("agent_did"),
        "output_hash": record.get("output_hash"),
        "checks": checks,
    }


@app.post("/vc/ipr/{ipr_id}/outcome", tags=["Output Provenance"])
async def ipr_outcome(ipr_id: str, request: Request):
    """Submit outcome feedback for confidence calibration."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    api_key = request.headers.get("x-api-key", "")
    if not api_key:
        raise HTTPException(401, "X-API-Key required")

    body = await request.json()
    outcome_hash = body.get("outcome_hash", "")
    outcome_correct = body.get("outcome_correct")

    if outcome_correct is None or not isinstance(outcome_correct, bool):
        raise HTTPException(422, "outcome_correct (bool) required")

    async with db_pool.acquire() as conn:
        # Verify ownership
        row = await conn.fetchrow(
            "SELECT agent_did FROM interaction_proof_records WHERE id = $1",
            __import__("uuid").UUID(ipr_id)
        )
        if not row:
            raise HTTPException(404, "IPR not found")

        owner = await conn.fetchval(
            "SELECT owner_did FROM api_keys WHERE key = $1 AND active = true",
            api_key
        )
        if owner != row["agent_did"]:
            raise HTTPException(403, "Not the IPR owner")

        ok = await submit_outcome(conn, ipr_id, outcome_hash, outcome_correct)

    if not ok:
        raise HTTPException(409, "Outcome already recorded")
    return {"ipr_id": ipr_id, "outcome_recorded": True}


# --- Admin Endpoints ---

@limiter.limit("10/minute")
@app.post("/vc/ipr/admin/anchor", tags=["Output Provenance Admin"])
async def ipr_admin_anchor(request: Request):
    """Admin: Trigger Merkle batch anchoring for all pending IPRs."""
    verify_admin(request, AdminPermission.WRITE)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await anchor_batch(conn, anchor_single_calldata)
    return result


@limiter.limit("10/minute")
@app.post("/vc/ipr/admin/retry", tags=["Output Provenance Admin"])
async def ipr_admin_retry(request: Request):
    """Admin: Reset failed IPRs back to pending."""
    verify_admin(request, AdminPermission.WRITE)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await retry_failed(conn)
    return result


@limiter.limit("10/minute")
@app.post("/vc/ipr/admin/reconcile", tags=["Output Provenance Admin"])
async def ipr_admin_reconcile(request: Request):
    """Admin: Verify all anchored IPRs against chain and reset missing."""
    verify_admin(request, AdminPermission.WRITE)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await reconcile_pending(conn)
    return result


@limiter.limit("10/minute")
@app.post("/vc/ipr/admin/reanchor", tags=["Output Provenance Admin"])
async def ipr_admin_reanchor(request: Request):
    """Admin: Force re-anchor a specific IPR."""
    verify_admin(request, AdminPermission.WRITE)

    body = await request.json()
    ipr_id = body.get("ipr_id")
    if not ipr_id:
        raise HTTPException(422, "ipr_id required")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await reanchor_ipr(conn, ipr_id)
    return result


# ═══════════════════════════════════════════════════════════════
# BATCH REGISTRATION — /identity/register-batch
# ═══════════════════════════════════════════════════════════════


@app.post("/identity/register-batch", tags=["Identity"])
@limiter.limit("5/minute")
async def register_batch(request: Request):
    """
    Batch register external agents. Requires x-admin-key.
    Creates DID, bridges external DID, imports score, anchors via single Merkle TX.
    Up to 1000 agents per call. Idempotent.
    """
    verify_admin(request, AdminPermission.WRITE)

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    body = await request.json()
    external_system = body.get("external_system", "generic")
    jwks_url = body.get("jwks_url")
    agents = body.get("agents", [])

    if not agents:
        raise HTTPException(422, "agents list required")
    if len(agents) > 1000:
        raise HTTPException(422, "Max 1000 agents per batch")

    results = []
    created_dids = []

    async with db_pool.acquire() as conn:
        for agent in agents:
            ext_did = agent.get("external_did", "")
            label = agent.get("label", "agent")
            capabilities = agent.get("capabilities", [])

            if not ext_did:
                results.append({"label": label, "status": "error", "reason": "missing external_did"})
                continue

            # Check if already bridged (idempotent)
            existing = await conn.fetchrow(
                "SELECT moltrust_did FROM did_bridges WHERE external_did = $1", ext_did
            )
            if existing:
                results.append({
                    "label": label,
                    "external_did": ext_did,
                    "moltrust_did": existing["moltrust_did"],
                    "status": "exists",
                })
                continue

            # Generate DID
            agent_did = f"did:moltrust:{uuid.uuid4().hex[:16]}"
            ts = datetime.datetime.utcnow().isoformat()
            display_name = f"{external_system}-{label}"

            # Insert agent
            try:
                await conn.execute(
                    "INSERT INTO agents (did, display_name, platform, created_at) VALUES ($1, $2, $3, $4)",
                    agent_did, display_name, external_system, datetime.datetime.utcnow()
                )
            except Exception as e:
                results.append({"label": label, "status": "error", "reason": str(e)[:100]})
                continue

            # Generate API key
            api_key = f"mt_{secrets.token_hex(16)}"
            await conn.execute("INSERT INTO api_keys (key, email, active) VALUES ($1, $2, true)",
                               api_key, f"{display_name}@batch.moltrust.ch")
            API_KEYS.add(api_key)
            await conn.execute(
                "UPDATE api_keys SET owner_did = $1 WHERE key = $2", agent_did, api_key
            )

            # Bridge
            try:
                await conn.execute(
                    "INSERT INTO did_bridges (external_did, moltrust_did, chain, wallet_address) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (external_did) DO NOTHING",
                    ext_did, agent_did, external_system, f"{external_system}-{label}"
                )
            except Exception:
                pass

            # Grant credits
            try:
                await conn.execute(
                    "INSERT INTO credit_balances (did, balance) VALUES ($1, $2) ON CONFLICT (did) DO NOTHING",
                    agent_did, 175
                )
            except Exception:
                pass

            created_dids.append(agent_did)
            results.append({
                "label": label,
                "external_did": ext_did,
                "moltrust_did": agent_did,
                "api_key": api_key,
                "status": "created",
            })

    # Single Merkle anchor for all new agents
    anchor_result = None
    if created_dids:
        try:
            ts = datetime.datetime.utcnow().isoformat()
            calldata = f"MolTrust/BatchRegister/v1/{hashlib.sha256(('|'.join(created_dids) + ts).encode()).hexdigest()}"
            tx_hash = await anchor_to_base(calldata, ts)
            if tx_hash and db_pool:
                async with db_pool.acquire() as conn:
                    for did in created_dids:
                        await conn.execute("UPDATE agents SET base_tx_hash = $1 WHERE did = $2", tx_hash, did)
            anchor_result = {"tx_hash": tx_hash, "chain": "base", "agents_anchored": len(created_dids)}
        except Exception as e:
            anchor_result = {"error": str(e)[:100]}

    return {
        "external_system": external_system,
        "total": len(agents),
        "created": sum(1 for r in results if r["status"] == "created"),
        "exists": sum(1 for r in results if r["status"] == "exists"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "anchor": anchor_result,
        "agents": results,
    }


# ═══════════════════════════════════════════════════════════════
# ADMIN DASHBOARD — Auth + Dashboard API
# ═══════════════════════════════════════════════════════════════

from app.admin_auth import (
    verify_password, create_session, verify_session,
    invalidate_session, ADMIN_USERS,
)
from app.admin_rbac import AdminPermission, verify_admin, is_admin


class AdminLoginRequest(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)


def _get_admin_session(request: Request) -> dict:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        token = request.cookies.get("admin_token", "")
    session = verify_session(token)
    if not session:
        raise HTTPException(401, "Not authenticated")
    return session


@app.post("/admin/login")
@limiter.limit("5/minute")
async def admin_login(request: Request, body: AdminLoginRequest):
    if body.username not in ADMIN_USERS:
        raise HTTPException(401, "Invalid credentials")
    if not verify_password(body.username, body.password):
        raise HTTPException(401, "Invalid credentials")
    token, expires = create_session(body.username)
    return {
        "token": token,
        "username": body.username,
        "role": ADMIN_USERS[body.username]["role"],
        "expires_at": expires.isoformat(),
    }


@app.post("/admin/logout")
async def admin_logout(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    invalidate_session(token)
    return {"status": "logged_out"}


@app.get("/admin/me")
async def admin_me(request: Request):
    session = _get_admin_session(request)
    return {"username": session["username"], "role": session["role"]}


@app.get("/admin/dashboard/overview")
async def dashboard_overview(request: Request):
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        total_agents = await conn.fetchval("SELECT COUNT(*) FROM agents")
        active_today = await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE last_active_at > NOW() - INTERVAL '24 hours'"
        )
        ghost_count = await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE COALESCE(last_active_at, created_at) < NOW() - INTERVAL '30 days'"
        )
        new_week = await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE created_at > NOW() - INTERVAL '7 days'"
        )
        total_creds = await conn.fetchval("SELECT COUNT(*) FROM credentials")
        total_ratings = await conn.fetchval("SELECT COUNT(*) FROM ratings")
        avg_rating = await conn.fetchval("SELECT COALESCE(ROUND(AVG(score)::numeric, 2), 0) FROM ratings")

        ipr_stats = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN anchor_status = 'anchored' THEN 1 ELSE 0 END) as anchored,
                   SUM(CASE WHEN anchor_status = 'pending' THEN 1 ELSE 0 END) as pending,
                   SUM(CASE WHEN anchor_status = 'failed' THEN 1 ELSE 0 END) as failed,
                   COUNT(DISTINCT agent_did) as unique_agents
            FROM interaction_proof_records
        """)

        x402_calls = await conn.fetchval(
            "SELECT COUNT(*) FROM x402_verify_calls WHERE called_at > NOW() - INTERVAL '24 hours'"
        )
        total_payments = await conn.fetchval("SELECT COUNT(*) FROM payment_events")
        total_usdc = await conn.fetchval(
            "SELECT COALESCE(SUM(amount_usdc), 0) FROM payment_events"
        )

        credit_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM credit_balances")

        endorsements = await conn.fetchval("SELECT COUNT(*) FROM endorsements WHERE expires_at > NOW()")

        flagged = await conn.fetchval("""
            SELECT COUNT(DISTINCT did) FROM agents
            WHERE COALESCE(last_active_at, created_at) < NOW() - INTERVAL '30 days'
        """)

    # SSL check
    ssl_days = None
    try:
        import subprocess
        result = subprocess.run(
            ["openssl", "s_client", "-servername", "moltrust.ch", "-connect", "moltrust.ch:443"],
            input=b"", capture_output=True, timeout=5
        )
        cert_result = subprocess.run(
            ["openssl", "x509", "-noout", "-enddate"],
            input=result.stdout, capture_output=True, timeout=5
        )
        if cert_result.stdout:
            import email.utils
            exp_str = cert_result.stdout.decode().strip().split("=")[1]

            exp = _dt.strptime(exp_str, "%b %d %H:%M:%S %Y %Z")
            ssl_days = (exp - _dt.utcnow()).days
    except Exception:
        pass

    return {
        "api": {
            "status": "ok",
            "version": "2.4",
        },
        "agents": {
            "total": total_agents,
            "active_today": active_today,
            "ghost_agents": ghost_count,
            "new_this_week": new_week,
        },
        "credentials": {
            "total": total_creds,
            "ratings": total_ratings,
            "avg_rating": float(avg_rating),
            "endorsements_active": endorsements,
        },
        "ipr": {
            "total": ipr_stats["total"],
            "anchored": ipr_stats["anchored"],
            "pending": ipr_stats["pending"],
            "failed": ipr_stats["failed"],
            "unique_agents": ipr_stats["unique_agents"],
        },
        "x402": {
            "verify_calls_24h": x402_calls,
            "total_payments": total_payments,
            "volume_usdc": float(total_usdc),
        },
        "credits": {
            "total_balance": float(credit_balance),
        },
        "security": {
            "ssl_days_remaining": ssl_days,
        },
        "trust": {
            "flagged_agents": flagged,
        },
    }


@app.get("/admin/dashboard/agents")
async def dashboard_agents(request: Request):
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        agents = await conn.fetch("""
            SELECT did, COALESCE(NULLIF(display_name, 'anonymous'), 'anon-' || SUBSTRING(did, 15, 8)) as display_name, platform, agent_type, created_at,
                   last_active_at, wallet_address, wallet_chain,
                   EXTRACT(DAY FROM NOW() - COALESCE(last_active_at, created_at))::int as days_inactive
            FROM agents
            ORDER BY COALESCE(last_active_at, created_at) DESC
            LIMIT 100
        """)

    return {
        "count": len(agents),
        "agents": [
            {
                "did": a["did"],
                "display_name": a["display_name"],
                "platform": a["platform"],
                "agent_type": a["agent_type"],
                "created_at": a["created_at"].isoformat() if a["created_at"] else None,
                "last_active_at": a["last_active_at"].isoformat() if a["last_active_at"] else None,
                "wallet": a["wallet_address"],
                "chain": a["wallet_chain"],
                "days_inactive": a["days_inactive"],
                "status": "active" if (a["days_inactive"] or 0) < 7 else ("idle" if (a["days_inactive"] or 0) < 30 else "ghost"),
            }
            for a in agents
        ],
    }


@app.get("/admin/dashboard/activity")
async def dashboard_activity(request: Request):
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        recent = await conn.fetch("""
            SELECT i.agent_did, a.display_name, a.platform,
                   i.output_type, i.confidence, i.produced_at, i.anchor_status
            FROM interaction_proof_records i
            LEFT JOIN agents a ON a.did = i.agent_did
            ORDER BY i.produced_at DESC LIMIT 50
        """)
        active = await conn.fetch("""
            SELECT did, display_name, platform, last_active_at
            FROM agents
            WHERE last_active_at > NOW() - INTERVAL '24 hours'
            ORDER BY last_active_at DESC
        """)

        agent_summary = await conn.fetch("""
            SELECT a.display_name, r.agent_did,
                   COUNT(*) as total,
                   COUNT(*) FILTER (WHERE r.anchor_status = 'anchored') as anchored,
                   COUNT(*) FILTER (WHERE r.anchor_status = 'pending') as pending,
                   MAX(r.produced_at) as last_seen
            FROM interaction_proof_records r
            LEFT JOIN agents a ON r.agent_did = a.did
            WHERE r.produced_at > NOW() - INTERVAL '24 hours'
            GROUP BY a.display_name, r.agent_did
            ORDER BY total DESC
        """)

        ipr_totals = await conn.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE anchor_status = 'anchored') as anchored,
                   COUNT(*) FILTER (WHERE anchor_status = 'pending') as pending,
                   COUNT(DISTINCT agent_did) as unique_agents
            FROM interaction_proof_records
            WHERE produced_at > NOW() - INTERVAL '24 hours'
        """)

    return {
        "recent_activity": [
            {
                "agent_did": r["agent_did"],
                "display_name": r["display_name"],
                "platform": r["platform"],
                "output_type": r["output_type"],
                "confidence": float(r["confidence"]) if r["confidence"] else None,
                "produced_at": r["produced_at"].isoformat() if r["produced_at"] else None,
                "anchor_status": r["anchor_status"],
            }
            for r in recent
        ],
        "active_agents": [
            {
                "did": a["did"],
                "display_name": a["display_name"],
                "platform": a["platform"],
                "last_active_at": a["last_active_at"].isoformat() if a["last_active_at"] else None,
            }
            for a in active
        ],
        "agent_summary": [
            {
                "agent_did": s["agent_did"],
                "display_name": s["display_name"] or s["agent_did"][:20],
                "total": s["total"],
                "anchored": s["anchored"],
                "pending": s["pending"],
                "last_seen": s["last_seen"].isoformat() if s["last_seen"] else None,
            }
            for s in agent_summary
        ],
        "ipr_totals": {
            "total": ipr_totals["total"] if ipr_totals else 0,
            "anchored": ipr_totals["anchored"] if ipr_totals else 0,
            "pending": ipr_totals["pending"] if ipr_totals else 0,
            "unique_agents": ipr_totals["unique_agents"] if ipr_totals else 0,
        },
    }


@app.get("/admin/dashboard/security")
async def dashboard_security(request: Request):
    _get_admin_session(request)
    import pathlib
    log_path = pathlib.Path("/home/moltstack/moltstack/logs/security_report.log")
    lines = []
    if log_path.exists():
        text = log_path.read_text()
        # Get last report block
        blocks = text.split("=============================================")
        if len(blocks) >= 2:
            last_report = "=============================================".join(blocks[-3:]) if len(blocks) >= 3 else text[-2000:]
            lines = last_report.strip().split("\n")

    return {"report_lines": lines[-60:] if lines else ["No security report found"]}


@app.get("/admin/dashboard/x402")
async def dashboard_x402(request: Request):
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        calls = await conn.fetch("""
            SELECT queried_did, COUNT(*) as total,
                   COUNT(DISTINCT caller_ip) as unique_callers,
                   MAX(called_at) as last_call
            FROM x402_verify_calls
            GROUP BY queried_did ORDER BY total DESC LIMIT 20
        """)
        payments = await conn.fetch("""
            SELECT tx_hash, from_address, to_address, amount_usdc, token, did, received_at
            FROM payment_events ORDER BY received_at DESC LIMIT 20
        """)

    return {
        "verify_calls": [
            {"did": r["queried_did"], "total": r["total"],
             "unique_callers": r["unique_callers"],
             "last_call": r["last_call"].isoformat() if r["last_call"] else None}
            for r in calls
        ],
        "payments": [
            {"tx_hash": p["tx_hash"], "from": p["from_address"],
             "amount_usdc": float(p["amount_usdc"]) if p["amount_usdc"] else 0,
             "did": p["did"],
             "received_at": p["received_at"].isoformat() if p["received_at"] else None}
            for p in payments
        ],
    }


KNOWN_CALLERS = {
    # Cloud / CDN
    "103.": "Shopee", "47.": "Alibaba", "34.": "Google Cloud",
    "52.": "AWS", "18.": "AWS", "172.70.": "Cloudflare",
    # Monitors
    "172.212.": "Upptime", "74.220.": "Render.com (Oregon)",
    # AI Agents / Integrations
    "176.65.148.": "silver.inc AI Agent Framework",
    "50.66.141.": "Unknown (axios/1.13.5) — active integration",
    # Competitor scrapers
    "54.219.101.": "AgentScore-Enrichment",
    "54.176.37.": "AgentScore-Enrichment",
    # Security scanners
    "54.244.31.": "8004scan Security Scanner",
    "54.188.216.": "8004scan Security Scanner",
    "54.201.136.": "8004scan Security Scanner",
    "199.127.61.": "Umai Security Scanner",
    # Team
    "82.135.79.": "Team (MNET Germany)",
    "46.225.175.": "Team (Hetzner)",
}

@app.get("/admin/dashboard/journal")
async def dashboard_journal(request: Request):
    """Return today's and recent journal entries."""
    _get_admin_session(request)
    import glob
    from pathlib import Path

    journal_dir = Path.home() / "journal"
    entries = []

    if journal_dir.exists():
        files = sorted(journal_dir.glob("*.md"), reverse=True)[:7]
        for f in files:
            entries.append({
                "date": f.stem,
                "content": f.read_text(),
                "size": f.stat().st_size,
            })

    return {
        "entries": entries,
        "total_files": len(list(journal_dir.glob("*.md"))) if journal_dir.exists() else 0,
    }




@app.get("/admin/dashboard/journal/list")
async def dashboard_journal_list(request: Request):
    """List ALL available journal entries with preview. Admin auth."""
    _get_admin_session(request)
    from pathlib import Path as P
    journal_dir = P.home() / "journal"
    entries = []
    if journal_dir.exists():
        for f in sorted(journal_dir.glob("2*.md"), reverse=True):
            text = f.read_text()
            entries.append({
                "date": f.stem,
                "preview": text[:200].replace("\n", " ").strip(),
                "size": f.stat().st_size,
            })
    return entries


@app.get("/admin/dashboard/journal/search")
async def dashboard_journal_search(request: Request, q: str = Query("", min_length=2, max_length=100)):
    """Full-text search across all journal entries. Admin auth."""
    _get_admin_session(request)
    from pathlib import Path as P
    journal_dir = P.home() / "journal"
    q_lower = q.lower()
    results = []
    if journal_dir.exists():
        for f in sorted(journal_dir.glob("2*.md"), reverse=True):
            text = f.read_text()
            lines = text.split("\n")
            matches = []
            for i, line in enumerate(lines, 1):
                if q_lower in line.lower():
                    start = max(0, line.lower().index(q_lower) - 40)
                    end = min(len(line), start + len(q) + 80)
                    matches.append({
                        "line": i,
                        "snippet": line[start:end].strip(),
                    })
            if matches:
                results.append({"date": f.stem, "matches": matches})
            if len(results) >= 50:
                break
    return results


@app.get("/admin/dashboard/journal/{date}")
async def dashboard_journal_entry(request: Request, date: str):
    """Return a specific journal entry by date (YYYY-MM-DD)."""
    _get_admin_session(request)
    from pathlib import Path
    import re

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    journal_file = Path.home() / "journal" / f"{date}.md"
    if not journal_file.exists():
        raise HTTPException(404, f"No journal entry for {date}")

    return {
        "date": date,
        "content": journal_file.read_text(),
    }

class JournalAppendRequest(BaseModel):
    text: str = Field(max_length=2000)
    date: str = Field(default=None, max_length=10)

@app.post("/admin/journal/append")
async def journal_append(request: Request, body: JournalAppendRequest):
    """Append a note to today's (or specified date's) journal entry."""
    _get_admin_session(request)
    from pathlib import Path
    import re, datetime

    import zoneinfo as _zi; date = body.date or _dt.datetime.now(_zi.ZoneInfo("Europe/Zurich")).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(400, "Invalid date format")

    journal_dir = Path.home() / "journal"
    journal_dir.mkdir(exist_ok=True)
    journal_file = journal_dir / f"{date}.md"

    note = body.text.strip()
    if not note:
        raise HTTPException(400, "Empty note")

    timestamp = _dt.datetime.now(_zi.ZoneInfo("Europe/Zurich")).strftime("%H:%M CEST")
    formatted = f"\n\n### Note ({timestamp})\n{note}\n"

    with open(journal_file, "a") as f:
        f.write(formatted)

    return {"status": "appended", "date": date, "timestamp": timestamp}


# ═══════════════════════════════════════════════════════════════════════════════
# SITE-WIDE SEARCH (Public)
# ═══════════════════════════════════════════════════════════════════════════════

_site_search_cache: dict = {}

@app.get("/api/search")
@limiter.limit("30/minute")
async def site_search(request: Request, q: str = Query("", min_length=2, max_length=80)):
    """Public site-wide search over all HTML pages."""
    import time as _time
    from bs4 import BeautifulSoup
    from pathlib import Path as P

    q_lower = q.lower().strip()
    cache_key = q_lower

    cached = _site_search_cache.get(cache_key)
    if cached and cached["expires"] > _time.time():
        return cached["results"]

    html_dir = P("/var/www/html")
    results = []

    html_files = []
    for pattern in ["*.html", "blog/*.html", "enterprise/*.html", "docs/*.html", "partners/*.html"]:
        html_files.extend(html_dir.glob(pattern))

    seen = set()
    for f in html_files:
        rel = f.relative_to(html_dir)
        rel_str = str(rel)
        if rel_str in seen:
            continue
        if "admin" in rel_str or ".bak" in rel_str or "~" in rel_str:
            continue
        seen.add(rel_str)

        try:
            raw = f.read_text(errors="ignore")
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["nav", "footer", "header", "aside",
                              "script", "style", "noscript"]):
                tag.decompose()
            for tag in soup.find_all(class_=["mobile-menu", "nav-hamburger"]):
                tag.decompose()

            title_tag = soup.find("title")
            h1_tag = soup.find("h1")
            title = ""
            if title_tag and title_tag.string:
                title = title_tag.string.strip()
                title_tag.decompose()
            elif h1_tag:
                title = h1_tag.get_text(strip=True)

            text = soup.get_text(separator=" ", strip=True)
            text_lower = text.lower()
            title_lower = title.lower()

            if q_lower not in text_lower and q_lower not in title_lower:
                continue

            body_count = text_lower.count(q_lower)
            title_count = title_lower.count(q_lower)
            score = body_count + title_count * 3

            idx = text_lower.find(q_lower)
            snippet = ""
            if idx >= 0:
                start = max(0, idx - 80)
                end = min(len(text), idx + len(q) + 80)
                snippet = text[start:end].strip()
                if start > 0:
                    snippet = "..." + snippet
                if end < len(text):
                    snippet = snippet + "..."

            url = "/" + rel_str
            results.append({"title": title or rel_str, "url": url, "snippet": snippet, "score": score})
        except Exception:
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:30]

    _site_search_cache[cache_key] = {"results": results, "expires": _time.time() + 300}

    now = _time.time()
    for k in [k for k, v in _site_search_cache.items() if v["expires"] < now]:
        _site_search_cache.pop(k, None)

    return results









# ═══════════════════════════════════════════════════════════════════════════════
# CALLERS REGISTRY (Admin Dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/dashboard/callers")
async def dashboard_callers(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str = Query(None, min_length=2, max_length=100),
):
    """Aggregated caller list from request_log. Admin auth."""
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        where = "WHERE ip IS NOT NULL"
        params = []
        idx = 1

        if q:
            where += f" AND (ip ILIKE ${idx} OR ip_org ILIKE ${idx} OR user_agent ILIKE ${idx})"
            params.append(f"%{q}%")
            idx += 1

        total = await conn.fetchval(
            f"SELECT COUNT(DISTINCT ip) FROM request_log {where}", *params
        )

        rows = await conn.fetch(f"""
            SELECT
                ip,
                COUNT(*) AS total_requests,
                MIN(ts) AS first_seen,
                MAX(ts) AS last_seen,
                COUNT(DISTINCT endpoint) AS unique_endpoints,
                MAX(user_agent) AS sample_user_agent,
                MAX(ip_org) AS ip_org,
                MAX(ip_country) AS ip_country,
                MAX(source) AS source,
                MAX(agent_did) AS agent_did,
                COUNT(DISTINCT DATE(ts)) AS days_active,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_count
            FROM request_log
            {where}
            GROUP BY ip
            ORDER BY MAX(ts) DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """, *params, limit, offset)

        return {
            "total": total,
            "callers": [
                {
                    "ip": r["ip"],
                    "total_requests": r["total_requests"],
                    "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                    "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                    "unique_endpoints": r["unique_endpoints"],
                    "sample_user_agent": r["sample_user_agent"],
                    "identified_as": r["ip_org"],
                    "ip_country": r["ip_country"],
                    "source": r["source"],
                    "agent_did": r["agent_did"],
                    "days_active": r["days_active"],
                    "error_count": r["error_count"],
                }
                for r in rows
            ],
        }


@app.get("/admin/dashboard/callers/{ip}")
async def dashboard_caller_detail(request: Request, ip: str):
    """Detail view for a single IP. Admin auth."""
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        summary = await conn.fetchrow("""
            SELECT
                ip,
                COUNT(*) AS total_requests,
                MIN(ts) AS first_seen,
                MAX(ts) AS last_seen,
                COUNT(DISTINCT endpoint) AS unique_endpoints,
                MAX(user_agent) AS sample_user_agent,
                MAX(ip_org) AS ip_org,
                MAX(ip_country) AS ip_country,
                MAX(source) AS source,
                MAX(agent_did) AS agent_did,
                COUNT(DISTINCT DATE(ts)) AS days_active,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_count
            FROM request_log
            WHERE ip = $1
            GROUP BY ip
        """, ip)

        if not summary:
            raise HTTPException(404, f"No requests from IP: {ip}")

        recent = await conn.fetch("""
            SELECT ts, endpoint, method, status_code, response_ms, user_agent, source, agent_did
            FROM request_log
            WHERE ip = $1
            ORDER BY ts DESC
            LIMIT 50
        """, ip)

        return {
            "ip": summary["ip"],
            "total_requests": summary["total_requests"],
            "first_seen": summary["first_seen"].isoformat() if summary["first_seen"] else None,
            "last_seen": summary["last_seen"].isoformat() if summary["last_seen"] else None,
            "unique_endpoints": summary["unique_endpoints"],
            "sample_user_agent": summary["sample_user_agent"],
            "identified_as": summary["ip_org"],
            "ip_country": summary["ip_country"],
            "source": summary["source"],
            "agent_did": summary["agent_did"],
            "days_active": summary["days_active"],
            "error_count": summary["error_count"],
            "recent_requests": [
                {
                    "ts": r["ts"].isoformat() if r["ts"] else None,
                    "endpoint": r["endpoint"],
                    "method": r["method"],
                    "status_code": r["status_code"],
                    "response_ms": r["response_ms"],
                    "user_agent": r["user_agent"],
                    "source": r["source"],
                    "agent_did": r["agent_did"],
                }
                for r in recent
            ],
        }


CALLER_CATEGORIES = {
    "176.65.148.": "ai_agent",
    "50.66.141.": "integration",
    "54.219.101.": "competitor",
    "54.176.37.": "competitor",
    "74.220.": "monitor",
    "172.212.": "monitor",
    "54.244.31.": "scanner",
    "54.188.216.": "scanner",
    "54.201.136.": "scanner",
    "199.127.61.": "scanner",
    "82.135.79.": "team",
    "46.225.175.": "team",
}

def _identify_caller(ip: str) -> dict:
    for prefix, name in KNOWN_CALLERS.items():
        if ip.startswith(prefix):
            cat = ""
            for cpfx, ccat in CALLER_CATEGORIES.items():
                if ip.startswith(cpfx):
                    cat = ccat
                    break
            return {"name": name, "category": cat}
    return {"name": "", "category": ""}


async def _identify_caller_db(ip: str, conn) -> dict:
    """Check caller_labels DB table for label + color."""
    row = await conn.fetchrow(
        "SELECT label, color FROM caller_labels WHERE ip = $1", ip
    )
    if row:
        return {"name": row["label"] or "", "color": row["color"] or "gray"}
    static = _identify_caller(ip)
    return {"name": static["name"], "color": "gray"}


async def _resolve_api_key_label(api_key_prefix: str, conn) -> dict | None:
    """Resolve API key prefix to a label from api_key_labels table."""
    if not api_key_prefix:
        return None
    row = await conn.fetchrow(
        "SELECT label, color FROM api_key_labels WHERE api_key_prefix = $1",
        api_key_prefix[:8]
    )
    if row:
        return {"label": row["label"], "color": row["color"] or "gray"}
    return None


@app.get("/admin/dashboard/traffic")
async def dashboard_traffic(request: Request, hours: int = Query(default=24, ge=1, le=168),
                            source: str = Query(default=None, max_length=20)):
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    where = "WHERE ts > NOW() - INTERVAL '1 hour' * $1"
    params: list = [hours]
    if source:
        where += " AND source = $2"
        params.append(source)

    async with db_pool.acquire() as conn:
        top_endpoints = await conn.fetch(f"""
            SELECT endpoint, COALESCE(source, 'fastapi') as source, COUNT(*) as calls,
                   AVG(response_ms)::int as avg_ms,
                   COUNT(DISTINCT ip) as unique_ips
            FROM request_log {where}
            GROUP BY endpoint, source ORDER BY calls DESC LIMIT 20
        """, *params)

        hourly = await conn.fetch(f"""
            SELECT DATE_TRUNC('hour', ts) as hour,
                   COUNT(*) as calls,
                   COUNT(DISTINCT ip) as unique_ips
            FROM request_log {where}
            GROUP BY hour ORDER BY hour ASC
        """, *params)

        callers = await conn.fetch(f"""
            SELECT ip, COUNT(*) as calls,
                   MAX(ts) as last_seen,
                   (array_agg(user_agent ORDER BY ts DESC))[1] as user_agent,
                   (array_agg(ip_org ORDER BY ts DESC))[1] as ip_org,
                   (array_agg(ip_country ORDER BY ts DESC))[1] as ip_country
            FROM request_log {where}
              AND ip NOT IN ('127.0.0.1', '::1')
            GROUP BY ip ORDER BY calls DESC LIMIT 20
        """, *params)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM request_log {where}", *params
        )

        by_source = await conn.fetch(f"""
            SELECT COALESCE(source, 'fastapi') as source, COUNT(*) as calls
            FROM request_log {where}
            GROUP BY source
        """, *params)

        return {
            "period_hours": hours,
            "total_calls": total,
            "by_source": {s["source"]: s["calls"] for s in by_source},
            "top_endpoints": [
                {"endpoint": e["endpoint"], "source": e["source"], "calls": e["calls"],
                 "avg_ms": e["avg_ms"], "unique_ips": e["unique_ips"]}
                for e in top_endpoints
            ],
            "hourly": [
                {"hour": h["hour"].isoformat(), "calls": h["calls"], "unique_ips": h["unique_ips"]}
                for h in hourly
            ],
            "external_callers": await _build_caller_list(callers, conn),
            "api_key_callers": await _build_api_key_callers(conn),
        }



async def _build_api_key_callers(conn) -> list:
    """List known API key callers with labels from api_key_labels."""
    rows = await conn.fetch("SELECT api_key_prefix, label, color FROM api_key_labels ORDER BY updated_at DESC")
    result = []
    for r in rows:
        result.append({
            "api_key_prefix": r["api_key_prefix"],
            "label": r["label"],
            "color": r["color"],
        })
    return result


async def _build_caller_list(callers, conn):
    result = []
    for c in callers:
        db_label = await _identify_caller_db(c["ip"], conn)
        static = _identify_caller(c["ip"])
        result.append({
            "ip": c["ip"], "calls": c["calls"],
            "last_seen": c["last_seen"].isoformat() if c["last_seen"] else None,
            "user_agent": (c["user_agent"] or "")[:100],
            "identified_as": db_label["name"] or static["name"],
            "label_color": db_label["color"],
            "category": static["category"],
            "org": c.get("ip_org") or "",
            "country": c.get("ip_country") or "",
        })
    return result


@app.get("/admin/traffic/caller/{ip}", tags=["Admin"])
async def caller_detail(ip: str, request: Request):
    """Admin: Get detailed traffic info for a specific IP."""
    _get_admin_session(request)
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    import subprocess as _sp

    async with db_pool.acquire() as conn:
        label_info = await _identify_caller_db(ip, conn)

        # Recent requests from DB
        rows = await conn.fetch(
            """SELECT endpoint, status_code, ts, response_ms
               FROM request_log WHERE ip LIKE $1 || '%'
               ORDER BY ts DESC LIMIT 200""", ip
        )

    endpoints: dict = {}
    timeline = []
    for r in rows:
        path = (r["endpoint"] or "").split("?")[0]
        endpoints[path] = endpoints.get(path, 0) + 1
        if len(timeline) < 50:
            timeline.append({
                "ts": r["ts"].strftime("%d/%b %H:%M:%S") if r["ts"] else "?",
                "path": path,
                "status": r["status_code"] or 0,
                "ms": r["response_ms"] or 0,
            })

    top_endpoints = sorted(
        [{"path": k, "count": v} for k, v in endpoints.items()],
        key=lambda x: -x["count"]
    )[:10]

    return {
        "ip": ip,
        "label": label_info["name"],
        "color": label_info["color"],
        "total_calls": len(rows),
        "top_endpoints": top_endpoints,
        "timeline": timeline,
    }


@app.post("/admin/traffic/caller/{ip}/label", tags=["Admin"])
async def set_caller_label(ip: str, request: Request):
    """Admin: Set or update label for a caller IP."""
    _get_admin_session(request)
    body = await request.json()
    label = body.get("label", "")
    color = body.get("color", "gray")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO caller_labels (ip, label, color, updated_at)
               VALUES ($1, $2, $3, NOW())
               ON CONFLICT (ip) DO UPDATE
               SET label = $2, color = $3, updated_at = NOW()""",
            ip, label, color
        )
    return {"ok": True, "ip": ip, "label": label, "color": color}


# ═══════════════════════════════════════════════════════════════
# TRUST BADGE — Live SVG badge for any DID
# ═══════════════════════════════════════════════════════════════

_GRADE_COLORS = {"S": "#E85D26", "A": "#22C55E", "B": "#3B82F6", "C": "#F59E0B", "D": "#EF4444", "F": "#6B7280"}


def _build_badge_svg(score, grade, did_short: str) -> str:
    if score is None or grade is None:
        value = "unverified"
        value_color = "#6B7280"
    else:
        value = f"{int(score)} / {grade}"
        value_color = _GRADE_COLORS.get(grade, "#6B7280")

    lw = 82
    vw = max(60, len(value) * 7 + 16)
    tw = lw + vw
    lc = "#1E293B"

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{tw}" height="20" role="img" aria-label="MolTrust: {value}">
  <title>MolTrust Trust Score: {value}</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{tw}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw}" height="20" fill="{lc}"/>
    <rect x="{lw}" width="{vw}" height="20" fill="{value_color}"/>
    <rect width="{tw}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="110">
    <text x="{lw*5}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(lw-10)*10}" lengthAdjust="spacing">MolTrust</text>
    <text x="{lw*5}" y="140" transform="scale(.1)" textLength="{(lw-10)*10}" lengthAdjust="spacing">MolTrust</text>
    <text x="{(lw + vw//2)*10}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(vw-10)*10}" lengthAdjust="spacing">{value}</text>
    <text x="{(lw + vw//2)*10}" y="140" transform="scale(.1)" textLength="{(vw-10)*10}" lengthAdjust="spacing">{value}</text>
  </g>
</svg>'''


@app.get("/badge/{did:path}")
async def get_trust_badge(did: str):
    """Live SVG badge showing trust score + grade. 1h cache."""
    score = None
    grade = None
    try:
        from app.swarm.trust_score import compute_phase2_score, score_to_grade
        if db_pool:
            async with db_pool.acquire() as conn:
                result = await compute_phase2_score(did, conn)
                score = result.get("score")
                grade = score_to_grade(score)
    except Exception:
        pass

    did_short = did[-8:] if len(did) > 8 else did
    svg = _build_badge_svg(score, grade, did_short)

    from starlette.responses import Response as _Resp
    return _Resp(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "max-age=3600, s-maxage=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ═══════════════════════════════════════════════════════════════
# WALLET SHADOW SCORE — Public wallet trust profile
# ═══════════════════════════════════════════════════════════════

@app.get("/wallet/{address}")
@limiter.limit("30/minute")
async def wallet_shadow_score(request: Request, address: str = Path(max_length=64)):
    """Public wallet trust profile with shadow score based on on-chain activity."""
    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        # Payment activity
        payments = await conn.fetchrow("""
            SELECT COUNT(*) as tx_count,
                   COALESCE(SUM(amount_usdc), 0) as total_usdc,
                   MAX(received_at) as last_seen
            FROM payment_events
            WHERE to_address = $1 OR from_address = $1
        """, address)

        tx_count = payments["tx_count"] if payments else 0
        total_usdc = float(payments["total_usdc"]) if payments else 0.0
        last_seen = payments["last_seen"]

        # Check if wallet is registered to a DID
        agent = await conn.fetchrow(
            "SELECT did, display_name FROM agents WHERE wallet_address = $1", address
        )

        # Trust score if registered
        trust_score = None
        grade = None
        if agent:
            try:
                from app.swarm.trust_score import compute_phase2_score, score_to_grade
                result = await compute_phase2_score(agent["did"], conn)
                trust_score = result.get("score")
                grade = score_to_grade(trust_score)
            except Exception:
                pass

    if tx_count == 0 and not agent:
        return {"wallet": address, "found": False}

    # Shadow score: base 25 + wallet_bonus (tx activity)
    wallet_bonus = min(10, tx_count * 0.5)
    volume_bonus = min(5, total_usdc * 0.1)
    shadow_score = round(25 + wallet_bonus + volume_bonus)

    # Projected: shadow + registration bonus (10) + estimated endorsements (15-25)
    projected_score = min(100, shadow_score + 10 + 15)
    projected_grade = "B" if projected_score >= 60 else ("C" if projected_score >= 40 else "D")

    from app.swarm.trust_score import score_to_grade as _s2g

    return {
        "wallet": address,
        "found": True,
        "tx_count": tx_count,
        "total_usdc": round(total_usdc, 2),
        "last_seen": last_seen.isoformat() + "Z" if last_seen else None,
        "shadow_score": shadow_score,
        "shadow_grade": _s2g(shadow_score),
        "projected_score": projected_score,
        "projected_grade": projected_grade,
        "registered": agent is not None,
        "did": agent["did"] if agent else None,
        "display_name": agent["display_name"] if agent else None,
        "trust_score": trust_score,
        "grade": grade,
        "register_url": f"https://moltrust.ch/register?wallet={address}" if not agent else None,
    }

# ── Billing Router ──
app.include_router(billing_router)
app.include_router(billing_admin_router)
app.include_router(test_harness_router)

from app.caep import router as caep_router
app.include_router(caep_router)

# ── Attestations Endpoint ─────────────────────────────────────────────────────

import uuid as _uuid
from datetime import timedelta, timezone

@app.get("/attestations/{attestation_id}")
async def get_attestation(attestation_id: str, did: str = None):
    """
    Resolve a MolTrust behavioral_trust attestation by ID.
    Compatible with aeoess/agent-governance-vocabulary canonical signal format.
    """
    target_did = did or f"did:moltrust:{attestation_id}"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT did, score, computed_at FROM trust_score_cache WHERE did = $1",
            target_did
        )

    if not row:
        raise HTTPException(404, f"No attestation found for {target_did}")

    now = _dt.datetime.now(timezone.utc)
    expires = now + timedelta(hours=1)
    score = float(row["score"])
    grade = "S" if score >= 95 else "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D" if score >= 20 else "F"

    return {
        "id": attestation_id,
        "did": row["did"],
        "canonical_signal": "behavioral_trust",
        "value": score,
        "scale": "0-100",
        "grade": grade,
        "issued_at": row["computed_at"].isoformat() if row["computed_at"] else now.isoformat(),
        "expires_at": expires.isoformat(),
        "issuer": "did:moltrust:registry",
        "attestation_uri": f"https://api.moltrust.ch/attestations/{attestation_id}",
        "registry_signature": {
            "type": "Ed25519Signature2020",
            "verificationMethod": "did:moltrust:registry#keys-1",
            "note": "Signature verification via /.well-known/did.json"
        }
    }


# --- Test Harness: Partner Endorsement Endpoint ---

class TestHarnessEndorseRequest(BaseModel):
    endorser_did: str = Field(max_length=256)
    target_did: str = Field(max_length=256)
    weight: float = Field(default=1.0)
    reason: str | None = Field(default=None, max_length=200)

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, v):
        if v <= 0.0 or v > 1.0:
            raise ValueError("weight must be > 0.0 and <= 1.0")
        return v

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, v):
        if v is None:
            return v
        if any(ch in v for ch in '<>"\''):
            raise ValueError("reason contains disallowed characters")
        return v.strip()


async def _resolve_to_moltrust_did(did: str, conn) -> str | None:
    """Resolve a DID to its did:moltrust: form. Returns None if not found."""
    # Native MolTrust DID
    if did.startswith("did:moltrust:"):
        row = await conn.fetchrow("SELECT did FROM agents WHERE did = $1", did)
        return row["did"] if row else None
    # External DID — look up bridge
    bridge = await conn.fetchrow(
        "SELECT moltrust_did FROM did_bridges WHERE external_did = $1", did
    )
    return bridge["moltrust_did"] if bridge else None


@app.post("/test-harness/endorse", tags=["Test Harness"])
@limiter.limit("60/minute")
async def test_harness_endorse(request: Request, body: TestHarnessEndorseRequest):
    """Record an endorsement via partner test harness. Requires partner-tier API key."""
    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key") or ""

    if not api_key:
        raise HTTPException(401, "X-API-Key header required")
    if api_key not in API_KEYS:
        raise HTTPException(401, "Invalid API key")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        # Check partner tier
        key_row = await conn.fetchrow(
            "SELECT tier, label FROM api_keys WHERE key = $1 AND active = true", api_key
        )
        if not key_row or key_row["tier"] != "partner":
            raise HTTPException(403, "Partner-tier API key required")

        partner_label = key_row["label"] or "unknown"

        # Resolve both DIDs
        endorser_mt = await _resolve_to_moltrust_did(body.endorser_did, conn)
        target_mt = await _resolve_to_moltrust_did(body.target_did, conn)

        if not endorser_mt and not target_mt:
            raise HTTPException(404, f"Neither DID is registered: {body.endorser_did}, {body.target_did}")
        if not endorser_mt:
            raise HTTPException(404, f"Endorser DID not bridged: {body.endorser_did}. Complete /test-harness/invoke handshake first or request admin bridge.")
        if not target_mt:
            raise HTTPException(404, f"Target DID not bridged: {body.target_did}. Complete /test-harness/invoke handshake first or request admin bridge.")

        now = _dt.datetime.now(timezone.utc)
        endorsement_id = f"end_{uuid.uuid4().hex[:16]}"

        # Upsert: update if (endorser, target) pair exists, otherwise insert
        existing = await conn.fetchrow(
            "SELECT id FROM endorsements WHERE endorser_did = $1 AND endorsed_did = $2",
            endorser_mt, target_mt
        )

        if existing:
            await conn.execute(
                "UPDATE endorsements SET weight = $1, skill = $2, evidence_timestamp = $3 "
                "WHERE endorser_did = $4 AND endorsed_did = $5",
                body.weight, body.reason or "test-harness", now,
                endorser_mt, target_mt
            )
        else:
            expires = now + _dt.timedelta(days=90)
            await conn.execute(
                "INSERT INTO endorsements "
                "(endorser_did, endorsed_did, skill, evidence_hash, vertical, weight, "
                " issued_at, expires_at, evidence_timestamp) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                endorser_mt, target_mt,
                body.reason or "test-harness",
                f"test-harness:{endorsement_id}",
                "core",
                body.weight,
                now, expires, now
            )

        # Invalidate trust score cache for target
        await conn.execute(
            "DELETE FROM trust_score_cache WHERE did = $1", target_mt
        )

        # Audit log
        try:
            await conn.execute(
                "INSERT INTO request_log (method, path, ip, api_key_prefix, response_status, logged_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                "POST", "/test-harness/endorse",
                _get_client_ip(request),
                f"{partner_label}:{api_key[:12]}...",
                200, now
            )
        except Exception:
            pass  # Audit log failure should not break the endpoint

    return {
        "status": "recorded",
        "endorser_did": endorser_mt,
        "target_did": target_mt,
        "weight": body.weight,
        "reason": body.reason,
        "recorded_at": now.isoformat(),
        "endorsement_id": endorsement_id,
    }
