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

app = FastAPI(title="MolTrust API", version="2.6", docs_url=None)

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
API_KEYS = set(os.getenv("MOLTRUST_API_KEYS", "mt_test_key_2026").split(","))
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
            host="localhost", database=os.getenv("DB_NAME", "moltstack"),
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

    # Check balance
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

    # Deduct only on success
    if response.status_code < 400:
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    from app.credits import resolve_endpoint_key
                    ref = resolve_endpoint_key(method, path)
                    await deduct_credits(conn, caller_did, cost, ref)
        except ValueError:
            pass  # race condition — balance already checked above
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
            await conn.execute(
                "INSERT INTO agents (did, display_name, platform, agent_type, created_at) VALUES ($1, $2, $3, 'external', $4)",
                agent_did, body.display_name, body.platform, datetime.datetime.utcnow()
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
    async with db_pool.acquire() as conn:
        try:
            result = await compute_phase2_score(did, conn)
            cached = await conn.fetchrow(
                "SELECT computed_at, cache_valid_until "
                "FROM trust_score_cache WHERE did = $1", did
            )
            return {
                "did": did,
                "trust_score": result["score"],
                "grade": score_to_grade(result["score"]),
                "breakdown": {
                    "direct_score": result["direct_score"],
                    "propagated_score": result["propagated_score"],
                    "cross_vertical_bonus": result["cross_vertical_bonus"],
                    "interaction_bonus": result["interaction_bonus"],
                    "sybil_penalty": result["sybil_penalty"],
                    "computation_method": result["computation_method"],
                },
                "endorser_count": result["endorser_count"],
                "withheld": result["withheld"],
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
        "version": "2.2",
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
                    "SELECT did, display_name, platform, created_at FROM agents WHERE did = $1", did
                )
                if row:
                    await update_last_seen(did)
                if row:
                    return {
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
        raise HTTPException(404, "DID not found")
    if did.startswith("did:web:"):
        raise HTTPException(501, "External did:web resolution not yet supported")
    raise HTTPException(400, "Unsupported DID method")
# --- Verifiable Credentials ---
from app.credentials import issue_credential, verify_credential

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

@app.get("/auth/github")
@limiter.limit("10/minute")
async def github_auth_start(request: Request):
    """Redirect to GitHub OAuth"""
    if GITHUB_CLIENT_ID == "PENDING":
        raise HTTPException(503, "GitHub OAuth not yet configured")
    return JSONResponse({"redirect_url": f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&scope=read:user"})

@app.get("/auth/github/callback")
@limiter.limit("10/minute")
async def github_auth_callback(request: Request, code: str = Query(max_length=128)):
    if GITHUB_CLIENT_ID == "PENDING":
        raise HTTPException(503, "GitHub OAuth not yet configured")
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
    result = verify_usdc_transfer(body.tx_hash)
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
@limiter.limit("60/minute")
async def a2a_agent_card(request: Request):
    return {
        "name": "MolTrust",
        "description": "Trust Layer for the Agent Economy. Identity verification, reputation scoring, and W3C Verifiable Credentials for AI agents.",
        "url": "https://api.moltrust.ch",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False
        },
        "skills": [
            {
                "id": "identity-verification",
                "name": "Agent Identity Verification",
                "description": "Register and verify W3C DID identities for AI agents with Ed25519 cryptographic signatures",
                "tags": ["identity", "did", "verification", "w3c"]
            },
            {
                "id": "reputation-scoring",
                "name": "Reputation Scoring",
                "description": "Query and submit trust ratings for AI agents. 1-5 scale with comment support.",
                "tags": ["reputation", "trust", "rating"]
            },
            {
                "id": "verifiable-credentials",
                "name": "Verifiable Credentials",
                "description": "Issue and verify W3C Verifiable Credentials signed with Ed25519",
                "tags": ["credentials", "w3c", "ed25519"]
            },
            {
                "id": "blockchain-anchor",
                "name": "Base Blockchain Anchoring",
                "description": "Anchor agent identity hashes on Base (Ethereum L2) for immutable proof",
                "tags": ["blockchain", "base", "ethereum", "anchor"]
            }
        ],
        "authentication": {
            "schemes": ["apiKey"],
            "apiKey": {"headerName": "X-API-Key", "signupUrl": "https://moltrust.ch#signup"}
        },
        "provider": {
            "organization": "CryptoKRI GmbH",
            "url": "https://moltrust.ch"
        },
        "links": {
            "docs": "https://api.moltrust.ch/docs",
            "github": "https://github.com/MoltyCel/moltrust-sdk",
            "pypi": "https://pypi.org/project/moltrust/",
            "did": "https://api.moltrust.ch/.well-known/did.json"
        }
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
    result = resolve_onchain_agent(agent_id)
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
                result["moltrust_profile"] = f"https://api.moltrust.ch/identity/resolve/{row["did"]}"

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
    wallet_address: str = Field(max_length=42)
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
        onchain = resolve_onchain_agent(row["erc8004_agent_id"])
        if "error" not in onchain:
            result["onchain"] = onchain

    return result


@app.post("/identity/erc8004/validate")
@limiter.limit("5/minute")
async def erc8004_validate(request: Request, body: ERC8004ValidateRequest, api_key: str = Depends(verify_api_key)):
    """MolTrust as ERC-8004 validator: assess agent, issue VC, post on-chain feedback."""
    onchain = resolve_onchain_agent(body.erc8004_agent_id)
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
            "SELECT settled_at FROM sports_predictions WHERE commitment_hash = $1",
            commitment_hash,
        )
        if not row:
            raise HTTPException(404, "Commitment not found")
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
            raise ValueError(f"violation_type must be one of: {", ".join(sorted(VALID_VIOLATION_TYPES))}")
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
async def verify_delegation_chain(request: Request, body: DelegationChainRequest):
    """Validate delegation chain depth (max 8 hops). Tech Spec v0.2.2."""
    valid, depth = check_delegation_depth(body.credential_chain)
    if not valid:
        return JSONResponse(
            status_code=400,
            content={
                "error": "delegation_chain_too_deep",
                "message": f"Delegation chain exceeds maximum depth of 8 hops",
                "max_depth": 8,
                "actual_depth": depth,
            },
        )
    return {
        "valid": True,
        "depth": depth,
        "max_depth": 8,
    }


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
