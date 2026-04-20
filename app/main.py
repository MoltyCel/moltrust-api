import asyncio
import secrets
import hmac as _hmac
import json
from fastapi import FastAPI, HTTPException, Header, Request, Depends, Query, Path
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel, Field, field_validator
import uuid, datetime, datetime as _dt, httpx, os, re, asyncpg, json, asyncio, logging, time, hashlib, secrets


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
from app.provenance.ipr import (
    validate_ipr_input, insert_ipr, get_ipr,
    get_iprs_by_agent, get_ipr_stats, submit_outcome,
)
from app.provenance.anchor import anchor_batch, anchor_single_calldata
from app.provenance.confidence import (
    compute_calibration_score as _ipr_calibration,
    check_confidence_inflation as _ipr_inflation,
)
from app.provenance.reconcile import (
    check_ipr_status, reconcile_pending, retry_failed, reanchor_ipr,
)

app = FastAPI(title="MolTrust API", version="2.4", docs_url=None)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

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
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()[:50]
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

# --- Validation Helpers ---
DID_PATTERN = re.compile(r"^did:moltrust:[a-f0-9]{16}$")
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

# ─── Response Models (for OpenAPI docs) ────────────────────────────────────────

class VerifyResponse(BaseModel):
    did: str
    verified: bool
    reputation: float

class ReputationResponse(BaseModel):
    did: str
    score: float
    total_ratings: int

class RateResponse(BaseModel):
    model_config = {"populate_by_name": True}
    status: str
    from_did: str = Field(alias="from")
    to_did: str = Field(alias="to")
    score: int
    erc8004_tx: str | None = None

class TrustScoreBreakdown(BaseModel):
    direct_score: float
    propagated_score: float
    cross_vertical_bonus: float | int
    interaction_bonus: float | int
    sybil_penalty: float
    computation_method: str

class TrustScoreResponse(BaseModel):
    did: str
    trust_score: float | None
    grade: str
    breakdown: TrustScoreBreakdown
    endorser_count: int
    withheld: bool
    flags: list
    flag_count: int
    computed_at: str | None
    cache_valid_until: str | None

class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    timestamp: str

class DIDDocumentMetadata(BaseModel):
    display_name: str | None = None
    platform: str | None = None
    created: str | None = None
    trust_provider: str | None = None

class DIDDocumentResponse(BaseModel):
    model_config = {"populate_by_name": True}
    context: str = Field(alias="@context", default="https://www.w3.org/ns/did/v1")
    id: str
    controller: str
    metadata: DIDDocumentMetadata | None = None
    service: list | None = None
    verificationMethod: list | None = None


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

@app.get("/identity/verify/{did}", response_model=VerifyResponse)
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

@app.get("/reputation/query/{did}", response_model=ReputationResponse)
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


@app.get("/skill/trust-score/{did:path}", response_model=TrustScoreResponse)
async def get_trust_score(did: str):
    """Phase 2 Trust Score with breakdown. Free. 1h cache."""
    from app.swarm.trust_score import compute_phase2_score, score_to_grade
    from app.anomaly import compute_flags
    async with db_pool.acquire() as conn:
        try:
            result = await compute_phase2_score(did, conn)
            cached = await conn.fetchrow(
                "SELECT computed_at, cache_valid_until "
                "FROM trust_score_cache WHERE did = $1", did
            )
            flags = await compute_flags(did, result["score"] or 0, conn) if not result["withheld"] else []
            return {
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
                    "computation_method": result["computation_method"],
                },
                "endorser_count": result["endorser_count"],
                "withheld": result["withheld"],
                "flags": flags,
                "flag_count": len(flags),
                "computed_at": cached["computed_at"].isoformat() if cached else None,
                "cache_valid_until": cached["cache_valid_until"].isoformat() if cached else None,
            }
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
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
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

@app.get("/health", response_model=HealthResponse)
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

@app.get("/identity/resolve/{did:path}", response_model=DIDDocumentResponse)
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
                    "SELECT did, display_name, platform, created_at, wallet_address, wallet_chain, wallet_bound_at FROM agents WHERE did = $1", did
                )
                if row:
                    await update_last_seen(did)
                if row:
                    doc = {
                        "@context": "https://www.w3.org/ns/did/v1",
                        "id": row["did"],
                        "controller": "did:web:api.moltrust.ch",
                        "metadata": {
                            "display_name": row["display_name"],
                            "platform": row["platform"],
                            "created": str(row["created_at"]),
                            "trust_provider": "MolTrust"
                        }
                    }
                    if row["wallet_address"]:
                        chain = row["wallet_chain"] or "base"
                        svc_type = "SolanaPaymentService" if chain == "solana" else "PaymentService"
                        currency = "USDC" if chain != "solana" else "SOL"
                        doc["service"] = [{
                            "id": f"{row['did']}#payment",
                            "type": svc_type,
                            "serviceEndpoint": {
                                "address": row["wallet_address"],
                                "chain": chain,
                                "currency": currency,
                                "bound_at": row["wallet_bound_at"].isoformat() + "Z" if row["wallet_bound_at"] else None,
                            }
                        }]
                    return doc
        raise HTTPException(404, "DID not found")
    if did.startswith("did:web:"):
        raise HTTPException(501, "External did:web resolution not yet supported")
    raise HTTPException(400, "Unsupported DID method")
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
        caller_ip = request.client.host if request.client else None
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
    admin_key = request.headers.get("x-admin-key", "")
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(403, "Admin key required")

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
            "SELECT did, display_name, platform, created_at, wallet_address, wallet_chain, wallet_bound_at FROM agents WHERE did = $1",
            bridge["moltrust_did"]
        )
    if not row:
        raise HTTPException(404, "Bridged MolTrust DID not found")

    return {
        "external_did": external_did,
        "moltrust_did": bridge["moltrust_did"],
        "chain": bridge["chain"],
        "bridged_at": bridge["created_at"].isoformat() + "Z" if bridge["created_at"] else None,
        "document": {
            "@context": "https://www.w3.org/ns/did/v1",
            "id": row["did"],
            "controller": "did:web:api.moltrust.ch",
            "metadata": {
                "display_name": row["display_name"],
                "platform": row["platform"],
                "created": str(row["created_at"]),
                "trust_provider": "MolTrust",
            },
        },
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


@app.post("/identity/register-batch", tags=["Identity"])
async def register_batch(request: Request):
    """Batch-register external agents with Merkle anchoring. Requires ADMIN_KEY."""
    admin_key = request.headers.get("x-admin-key", "")
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(403, "Invalid or missing admin key")

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
async def credits_transfer(request: Request, body: CreditTransferRequest, api_key: str = Depends(verify_api_key)):
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
async def credits_deposit(request: Request, body: DepositRequest, api_key: str = Depends(verify_api_key)):
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

# --- A2A Agent Card Trust Extension ---

@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
@limiter.limit("60/minute")
async def a2a_agent_card(request: Request):
    """A2A v0.3 conformant Agent Card with MolTrust trust-score extension."""
    return {
        "name": "MolTrust Trust Registry",
        "description": "W3C DID/VC trust infrastructure for autonomous AI agents. Provides cryptographic identity verification, behavioral trust scoring, and on-chain provenance anchoring on Base L2.",
        "url": "https://api.moltrust.ch",
        "version": "0.3",
        "provider": {
            "organization": "CryptoKRI GmbH",
            "url": "https://moltrust.ch",
            "contact": "info@moltrust.ch",
        },
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
            "extensions": [{
                "uri": "https://moltrust.ch/extensions/trust-score/v1",
                "description": "W3C DID-based agent trust scoring with on-chain behavioral history",
                "required": False,
                "params": {
                    "trust_score_endpoint": "https://api.moltrust.ch/skill/trust-score/{did}",
                    "min_score_header": "X-MolTrust-Min-Score",
                    "did_resolution": "https://api.moltrust.ch/identity/did/{did}",
                },
            }],
        },
        "securitySchemes": {
            "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            "moltrust": {
                "type": "apiKey", "in": "header", "name": "X-MolTrust-DID",
                "description": "Agent DID for trust-score gated endpoints",
            },
        },
        "security": [{"apiKey": []}, {"moltrust": []}],
        "skills": [
            {
                "id": "trust-score", "name": "Agent Trust Score",
                "description": "Returns W3C DID-based trust score (0-100) with behavioral history breakdown and on-chain proof",
                "tags": ["trust", "identity", "verification", "did", "w3c"],
                "examples": ["What is the trust score of did:moltrust:abc123?", "Verify this agent's behavioral history"],
                "inputModes": ["text"], "outputModes": ["text", "data"],
            },
            {
                "id": "did-resolution", "name": "DID Resolution",
                "description": "Resolves W3C Decentralized Identifiers to DID Documents with verification methods and service endpoints",
                "tags": ["did", "identity", "w3c", "resolution"],
                "examples": ["Resolve did:moltrust:abc123"],
                "inputModes": ["text"], "outputModes": ["data"],
            },
            {
                "id": "credential-verification", "name": "Verifiable Credential Verification",
                "description": "Verifies W3C Verifiable Credentials including Agent Authorization Envelopes (AAE) with delegation chain validation",
                "tags": ["vc", "credential", "aae", "delegation", "w3c"],
                "examples": ["Verify this agent's authorization credential", "Check if this AAE delegation chain is valid"],
                "inputModes": ["text", "data"], "outputModes": ["data"],
            },
            {
                "id": "wallet-binding", "name": "Wallet Binding Verification",
                "description": "Verifies cryptographic binding between agent DID and blockchain wallet address (EVM + Solana)",
                "tags": ["wallet", "payment", "base", "solana", "x402"],
                "examples": ["Is this agent payment-ready on Base L2?"],
                "inputModes": ["text"], "outputModes": ["data"],
            },
            {
                "id": "sybil-detection", "name": "Sybil & Anomaly Detection",
                "description": "Detects coordinated trust manipulation via endorsement-graph clustering and behavioral anomaly flags",
                "tags": ["security", "sybil", "anomaly", "fraud-detection"],
                "examples": ["Scan this agent cluster for sybil patterns"],
                "inputModes": ["text", "data"], "outputModes": ["data"],
            },
        ],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "data"],
    }


@app.get("/a2a/agent-card/{did}")
@limiter.limit("60/minute")
async def a2a_trust_card(request: Request, did: str = Path(max_length=128)):
    if not DID_PATTERN.match(did):
        raise HTTPException(status_code=400, detail="Invalid DID format")
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database unavailable")
    async with db_pool.acquire() as conn:
        agent = await conn.fetchrow("SELECT display_name, platform, created_at, base_tx_hash FROM agents WHERE did = $1", did)
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
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

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
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

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
            "SELECT * FROM violation_records WHERE agent_did = $1 ORDER BY created_at DESC",
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
            # Check admin
            admin_key = request.headers.get("x-admin-key", "")
            expected = os.environ.get("ADMIN_KEY", "")
            if not expected or admin_key != expected:
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
            "SELECT * FROM music_credentials WHERE agent_did = $1 ORDER BY issued_at DESC",
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
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not expected or admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")

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

@app.post("/vc/ipr/admin/anchor", tags=["Output Provenance Admin"])
async def ipr_admin_anchor(request: Request):
    """Admin: Trigger Merkle batch anchoring for all pending IPRs."""
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not admin_key or admin_key != expected:
        raise HTTPException(403, "Invalid admin key")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await anchor_batch(conn, anchor_single_calldata)
    return result


@app.post("/vc/ipr/admin/retry", tags=["Output Provenance Admin"])
async def ipr_admin_retry(request: Request):
    """Admin: Reset failed IPRs back to pending."""
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not admin_key or admin_key != expected:
        raise HTTPException(403, "Invalid admin key")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await retry_failed(conn)
    return result


@app.post("/vc/ipr/admin/reconcile", tags=["Output Provenance Admin"])
async def ipr_admin_reconcile(request: Request):
    """Admin: Verify all anchored IPRs against chain and reset missing."""
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not admin_key or admin_key != expected:
        raise HTTPException(403, "Invalid admin key")

    if not db_pool:
        raise HTTPException(503, "Database unavailable")

    async with db_pool.acquire() as conn:
        result = await reconcile_pending(conn)
    return result


@app.post("/vc/ipr/admin/reanchor", tags=["Output Provenance Admin"])
async def ipr_admin_reanchor(request: Request):
    """Admin: Force re-anchor a specific IPR."""
    admin_key = request.headers.get("x-admin-key")
    expected = os.environ.get("ADMIN_KEY", "")
    if not admin_key or admin_key != expected:
        raise HTTPException(403, "Invalid admin key")

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
    admin_key = request.headers.get("x-admin-key", "")
    expected = os.environ.get("ADMIN_KEY", "")
    if not admin_key or admin_key != expected:
        raise HTTPException(403, "Invalid admin key")

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
            from datetime import datetime as _dt
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
