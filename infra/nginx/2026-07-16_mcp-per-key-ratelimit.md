# nginx — MCP rate limit: per-`api_key` (with IP fallback) + per-IP safety net

**Date:** 2026-07-16 · **Status:** staged, **human-gated apply** (nginx is not repo-managed; `nginx -t` + reload need interactive sudo) · **Scope:** `location /mcp` only.

> Infra-as-code record per repo CLAUDE.md ("nginx nicht repo-verwaltet → manuelle Sorgfalt + Audit-Eintrag"). This file documents the exact change; the live apply is done by Lars via the block below.

## Why (evidence)

The `/mcp` traffic is proxied by **Smithery's hosted gateway, which runs on Cloudflare Workers** — so from nginx every request arrives from a handful of **Cloudflare-Worker egress IPs** (`172.68.22.26/.27`, in `172.64.0.0/13`). `api.moltrust.ch` itself is **DNS-direct** (not CF-proxied), so there is **no `CF-Connecting-IP` header** carrying the real end-user; whether Smithery forwards any per-user IP is unverified (see `docs/sprints/2026-05-12_smithery-v2-workflow.md §4.1`). The **only reliable per-user distinguisher is the `api_key`** (present as `?api_key=` on every authed request).

The old limit keyed on `$binary_remote_addr` → ~38 distinct users collapsed into ~2 buckets → **84 % false 429** (measured 12h: 13 978 / 16 649). Onset 2026-07-15 (flat ~170/day before, 0 rate-limited). Only **196 real tool interactions** reached the service in 12h (106 ListTools + 90 CallTool); the 14 k are client retries against the limit.

→ `real_ip_header CF-Connecting-IP` would **not** help (no such header here). Fix = key on `api_key`.

## The change

### 1) `/etc/nginx/nginx.conf` — in `http { }`, replace the single mcp zone (lines ~13–14)

REMOVE:
```nginx
    # MCP rate limit: ~100 requests/day per IP (burst 10)
    limit_req_zone $binary_remote_addr zone=mcp_unauth:10m rate=7r/m;
```
ADD:
```nginx
    # --- MCP rate limiting (per-api_key + IP fallback + per-IP safety net) ---
    # Authed MCP traffic arrives via Smithery's Cloudflare-Worker egress (a few
    # shared CF IPs), so per-IP limiting collapses ~38 users into ~2 buckets.
    # Key on api_key (only reliable per-user id); keyless → source IP fallback.
    map $arg_api_key $mcp_limit_key {
        ""      $binary_remote_addr;
        default $arg_api_key;
    }
    limit_req_zone $mcp_limit_key      zone=mcp_key:10m rate=60r/m;
    # Safety net: generous per-source-IP cap. Today's legit gateway peak is
    # ~134 req/min per CF IP, so 600r/min never trips normal traffic, but a
    # random-key flood from one source (which the per-key bucket can't stop) does.
    limit_req_zone $binary_remote_addr zone=mcp_ip:10m  rate=600r/m;
```

### 2) `/etc/nginx/sites-enabled/default` — in `location /mcp` (line ~212)

REMOVE:
```nginx
        limit_req zone=mcp_unauth burst=10 nodelay;
```
ADD (two directives — nginx applies **both** cumulatively; a request must pass each):
```nginx
        limit_req zone=mcp_key burst=30 nodelay;
        limit_req zone=mcp_ip  burst=100 nodelay;
```
(`limit_req_status 429;` on the next line stays.)

## Limit sizing (from measured data)

- **Per key: 60 r/min, burst 30.** One MCP interaction = POST(call) + GET(SSE) + DELETE(close) ≈ 3–5 requests; an interactive session with several tool calls = 15–40 req/min. `7 r/min` broke at a single interaction. 60 r/min + burst 30 covers a full session, caps a runaway key at ~1 req/s, and — per-key — one heavy user no longer starves the other 37.
- **Per IP: 600 r/min, burst 100 (safety net).** Today's legit peak is ~134 req/min for a single CF IP, so this never trips normal traffic; it exists to blunt a **random-key flood** from one source (distinct fake keys each get a fresh per-key bucket, so only the per-IP net stops them).
  - **Growth caveat:** if aggregate legit gateway traffic per CF IP approaches 600 r/min as MCP adoption grows, raise `mcp_ip` (it is a source-IP cap over *all* Smithery-multiplexed users).

## Apply (human-gated — Lars runs this)

See the copy-paste block delivered in-chat. Summary: timestamped backup of both files → patch (fail-safe: aborts if the anchor text isn't found) → `sudo nginx -t` (validates before any reload) → `sudo systemctl reload nginx`. Rollback = restore the `.bak-<ts>` files + reload.

## Verification after apply

- `curl -s -o /dev/null -w "%{http_code}" https://api.moltrust.ch/health` → 200 (nginx up).
- 12h/1h 429-rate on `/mcp` from the access log — **before: 84 %**, expected after: **≈ 0 %** (each key has its own 60 r/min bucket). Measured and appended to the report by the console once applied.

## Separate item (do NOT fix here) — api_key leaks in cleartext in `error_log`

`access.log` masks `api_key` (`map $request $request_masked` → `REDACTED`, since 2026-06-27). But `limit_req`'s `[error] limiting requests … request: "POST /mcp?…api_key=<real>"` lines go to **`error_log`, unmasked** — **15 404 cleartext api_key occurrences** currently. This fix cuts the volume (far fewer 429s) but not the mechanism. Own backlog item: mask/omit the key in error_log (nginx error_log can't be filtered like access_log — options: log-format-independent, so consider `error_log … crit;` for this path, or strip the query string upstream). Flagged, not changed.
