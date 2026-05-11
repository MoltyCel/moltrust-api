# Auto-Probe-Token — Pre-Deploy Report

**Sprint:** auto-probe-token security hardening
**Branch:** `feature/auto-probe-token`
**HEAD at report:** `9f9212b`
**Duration:** 2026-05-11, single-session
**Status:** ready for Phase 8 deploy review
**Verdict:** **GO-AFTER-CUTOVER-CHECKLIST**

This document closes out the sprint. It does **not** authorize deploy — that's a Phase-9 manual click by Lars. The role of this document is to capture what shipped, what is intentionally deferred, what to run during cutover, and what to do if cutover misbehaves.

---

## 1. Mount Architecture — FastMCP under FastAPI

**Commits:** `aff9f09`, `3dbbf51`

The standalone `services/mcp_http.py` standalone process (port 8002) was the prior attack surface: requests to `/mcp` bypassed every FastAPI middleware including `identity_middleware`, so an unauthenticated client could call any MCP tool the standalone instance had registered.

Mount changes:
- `app/main.py` now imports `moltrust_mcp_server.mcp` (the 39-tool PyPI singleton) and `app.mount("/mcp", _moltrust_mcp.streamable_http_app())` puts MCP under the same ASGI stack as the REST API.
- Lifespan composition: dedicated `@app.on_event("startup")` / `("shutdown")` pair drives `session_manager.run().__aenter__/__aexit__` alongside the existing DB-pool and scheduler hooks.
- `TransportSecuritySettings` pinned to ports 8000 + 8002 (see Section 3 Fix-2 for the tightening).
- `services/mcp_http.py` annotated as deprecated. Stays runnable until the Phase-8 nginx switch.

Verified end-to-end via uvicorn on port 8006:
- `initialize` → 200 + Mcp-Session-Id
- `notifications/initialized` → 202
- `tools/list` → 40 tools (39 from the PyPI singleton + the new `moltrust_identity` probe tool)
- A keyless first request mints a probe through `identity_middleware`, attributable in `probe_agents.first_seen_ip` / `probe_spawn` analytics.

---

## 2. Dispatch-Level Auth Matrix

**Commits:** `3dbbf51`, `0ae5ad2`, `b317b64`, `9f9212b`

`app/mcp_auth_middleware.py` (new) is a pure-ASGI middleware that intercepts every `POST /mcp` request, parses the JSON-RPC envelope, and on `tools/call` looks the tool name up in `app/mcp_auth_matrix.py` (new). The 39+1 tools live in three buckets per `docs/auto-probe-token-spec.md §6`:

| Tier | Count | Examples |
|---|---|---|
| `any` | 4 | `moltrust_stats`, `moltguard_market`, `moltguard_feed`, `moltrust_identity` |
| `probe` | 32 | most read-paths + self-VC issuance + register-as-probe |
| `claimed` | 4 | `moltrust_claim_deposit`, `mt_*_issue_vc` (cross-agent) |

Insufficient identity → JSON-RPC error envelope, code `-32001`, with `claim_url` in `data`. Unknown tool defaults to `claimed` (fail-closed). The 1 MB body cap (Fix-1) and the explicit-port `allowed_hosts` (Fix-2) sit on this middleware.

Coverage test in `tests/test_mcp_auth_matrix.py` asserts that every tool the running server registers has a matrix entry — adding a new MCP tool without a matrix entry breaks CI rather than silently defaulting probes out.

---

## 3. Six Security Fixes (Pass 1 review consensus)

| # | Commit | Finding | Fix |
|---|---|---|---|
| 1 | `88956b7` + test `2887476` | H6: `_client_ip` read `XFF[0]` → attacker-controlled | Prefer `X-Real-IP`, fall back to `XFF[-1]` |
| 2 | `e89dcbe` | H8: IPv6 rate-limit per-address → /48 attacker spawns unlimited | `bucket_subnet()` /24 IPv4 + /64 IPv6 |
| 3 | `996c174` | H11: probe key in response header → Sentry/APM logs cleartext | Stop emitting; key only via `/auth/identity` JSON body + `moltrust_identity` MCP tool |
| 4 | `2104f58` | H7-claim: parallel claims with same probe_key both mint identities | `SELECT ... FOR UPDATE` inside transaction, re-validate `claimed_at` |
| 5 | `619f12a` | H7-cap: parallel calls over-spend `call_count` cap | `UPDATE ... WHERE expires_at>now() AND call_count<call_cap RETURNING` — atomic |
| 6 | `43d875f` | H5: `user@dömäin.com` vs `user@xn--…` hash differently | `normalize_email()` — strip + NFC + lowercase + IDNA-encode domain |

All six **verified** by Pass 2 AI review (3/3 reviewer consensus) and by empirical curl tests where applicable.

---

## 4. Three Backlog Items (Perplexity Pass 1)

**Commit:** `ccef7cd`

- (a) **nginx `real_ip_header` / `set_real_ip_from`** — written up at `docs/ops/nginx-real-ip-policy.md`, **not applied to `/etc/nginx`**. No upstream proxy in front of nginx today, so `proxy_set_header X-Real-IP $remote_addr` already sets the trustworthy source. The doc includes a Cloudflare-ready snippet + rollout checklist for the day an edge proxy lands.
- (b) **Dependency pinning** — `requirements.txt` flipped from `>=` to `==` for every direct dep, anchored at currently-running versions. Stops PyPI hijack / next-resolve-different-pin from rolling silent breakage into a deploy.
- (c) **slowapi ≥ 0.1.9** — already satisfied (installed 0.1.9, requirements.txt already specced `>=0.1.9`). No action.

---

## 5. Re-Review — Split Bundles + Pass 2

**Run during this sprint, output preserved in `reviews/`:**

| Pass | Label | Output file |
|---|---|---|
| 1 | `auto-probe-rereview-A-rest` | `reviews/20260511_142136_auto-probe-rereview-A-rest_review.md` |
| 1 | `auto-probe-rereview-B-mcp` | `reviews/20260511_142300_auto-probe-rereview-B-mcp_review.md` |
| 2 | `auto-probe-pass2-verify` | `reviews/20260511_164216_auto-probe-pass2-verify_review.md` |

Pass 1 surfaced 3 real findings (body OOM, transport-wildcards, DB-fail-open) → committed as `0ae5ad2`, `b317b64`, `9f9212b`. Pass 2 verified those fixes with 3/3 reviewer consensus and empirical curl tests. Pass 2 surfaced one new finding documented under Backlog below.

Bundle split was necessary because the pipeline's hard 60k-char cutoff truncated tests + spec in Pass 1's earlier single-bundle attempt. The split kept Bundle A at 43 KB (REST/auth) and Bundle B at 32 KB (MCP) — both fully reviewed without truncation.

False-positives carried across passes (do not re-classify these):
- Cross-layer middleware ordering — `identity_middleware` runs BEFORE `McpAuthMiddleware` (empirically verified by introspecting `app.user_middleware`).
- `CVE-2025-2903` / `CVE-2026-1234` — hallucinated, not retrievable.
- `api_key=""` hash-oracle — empty string is falsy in `if api_key:`.
- Probe key in `moltrust_identity` body — intentional API contract per `auto-probe-token-spec.md §4.4`.

---

## 6. Backlog / Deferred Items

Each entry has an explicit reason for deferral. If a deploy reveals one of these is actually load-bearing, the entry tells you exactly where to start.

### B1 — `sys.path.insert(0, "/home/moltstack/moltstack/services")` in `app/main.py`

**Severity:** Low. **Source:** Pass 2 review finding.

The mount block at the top of `app/main.py` does `_mcp_sys.path.insert(0, "/home/moltstack/moltstack/services")` so the `from moltguard_mcp_tools import ...` lines resolve. Pass 2 reviewer flagged this as RCE-risk via "module hijacking".

**Threat-model assessment — not deploy-blocking:**

The path being inserted is `/home/moltstack/moltstack/services`. That directory is owned by the `moltstack` user — the same uid as the running FastAPI process. Any attacker who can write a file into that directory already has the same write capability against `/home/moltstack/moltstack/app/`, so they could replace `app/main.py` directly rather than play games with import order. The `sys.path.insert` does not widen the attack surface — it sits at the same trust boundary as the rest of the project code.

The clean refactor is to make `services/` a proper Python package (`__init__.py` + namespace via `app.services.moltguard_mcp_tools` or split into its own installable package). That's a 1–2 hour change touching every importer and the systemd `WorkingDirectory`. Schedule it as a hygiene PR after the auto-probe sprint lands; do not block the deploy on it.

### B2 — `bytes_seen` in 413 error response

**Severity:** Trivial. **Source:** Pass 2 review.

Pass 2 reviewer flagged that the 413 JSON-RPC error envelope returns `data.bytes_seen` (how much body was buffered before abort). This is not sensitive: the attacker already knows what they sent. Keep for the operator side — useful when diagnosing "why is my legitimate 800 KB request rejected" (they're not; the 413 only fires past 1 MB).

### B3 — CORS `allowed_origins` localhost wildcards

**Severity:** Low, accepted trade-off. **Source:** Pass 2 review (Gemini).

`TransportSecuritySettings.allowed_origins` still includes `http://127.0.0.1:*` + `http://localhost:*` because dev clients need to connect from ad-hoc browser ports during testing. The actual DNS-rebinding-protection knob is `allowed_hosts`, which IS pinned (Fix-2). Tightening origins would require a dev-vs-prod config split for limited additional defense.

### B4 — Probe-DID-based retry-loop rate limit

**Severity:** Low. **Source:** Pass 2 review.

If a probe DID receives a 429 from the `cap_or_ttl_exhausted` path and the client retry-loops without backoff, the server burns DB cycles checking the same probe. Currently mitigated by the slowapi `_ratelimit_key` (IP-bucketed) but a malicious client could spread retries across multiple probes from one IP. Defer until we see actual retry-loop pressure in `request_log` analytics.

### B5 — `services/mcp_http.py` standalone + `moltrust-mcp-http.service` retirement

**Severity:** Cleanup. **Status:** scheduled for Phase 8 cutover (see Section 7).

The legacy port-8002 process is still running and still listed in nginx `location /mcp`. The mount under FastAPI at port 8000 is functionally equivalent but the nginx switch + systemd disable have not been performed. Those are deploy operations, captured in Section 7.

### B6 — `app/mcp_auth_middleware.py` unit tests

**Severity:** Low. **Source:** Pass 2 review (P2).

`tests/test_mcp_auth_matrix.py` covers the matrix function and the middleware's allow/reject branches via a synthetic scope. It does not exercise the body-buffering path end-to-end via a real ASGI server. Sufficient for now (the body-cap path was empirically verified during Pass 2 with a 2 MB curl); add httpx-based integration tests if the matrix middleware sees further changes.

---

## 7. Deploy Commands — Phase 8 Cutover

Execute in order on the production server. **Do not run any of these from this report itself** — Lars's manual click ab Phase 9.

### 7a. Restart FastAPI process so it picks up the mount block

```bash
sudo systemctl restart moltstack.service
# Wait 5s for startup
sleep 5
# Verify both /health and /mcp are responding on port 8000
curl -s http://127.0.0.1:8000/health
SID=$(curl -s -i -X POST http://127.0.0.1:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"deploy-check","version":"0.1"}}}' \
  2>&1 | grep -i 'mcp-session-id:' | awk '{print $2}' | tr -d '\r\n')
echo "MCP session id (should be 32-char hex): $SID"
```

### 7b. Switch nginx /mcp proxy_pass from :8002 to :8000

```bash
# Edit /etc/nginx/sites-enabled/default line 182:
#   proxy_pass http://127.0.0.1:8002;
# →
#   proxy_pass http://127.0.0.1:8000;
sudo sed -i 's|proxy_pass http://127.0.0.1:8002;|proxy_pass http://127.0.0.1:8000;|' /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
# Verify the new path works via the public domain
SID=$(curl -s -i -X POST https://api.moltrust.ch/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"deploy-check","version":"0.1"}}}' \
  2>&1 | grep -i 'mcp-session-id:' | awk '{print $2}' | tr -d '\r\n')
echo "Public MCP session id: $SID"
# Tools/list — should return 40 tools (39 PyPI + moltrust_identity)
curl -s -X POST https://api.moltrust.ch/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
curl -s -X POST https://api.moltrust.ch/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 -c "import sys,json,re; m=re.search(r'data:\s*(\{.+\})',sys.stdin.read(),re.S); print('tool count:',len(json.loads(m.group(1))['result']['tools']))"
```

### 7c. Stop and disable the legacy standalone

```bash
sudo systemctl stop moltrust-mcp-http.service
sudo systemctl disable moltrust-mcp-http.service
sudo ss -tlnp | grep :8002 && echo "WARNING: still listening on 8002" || echo "8002 free"
```

### 7d. Smoke-test post-cutover

```bash
# Probe-flow happy path (mints fresh probe via /mcp)
curl -s -i -X POST https://api.moltrust.ch/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.1"}}}' \
  2>&1 | grep -i 'mcp-session-id'
# Should NOT see x-moltrust-probe-key header (Fix-3 / H11)
# Should see a 32-char Mcp-Session-Id

# Auth matrix happy path: probe-tier tool from a probe (use an existing
# probe_key, or mint one via /auth/identity first)
PK="$(curl -s https://api.moltrust.ch/auth/identity | python3 -c 'import sys,json; print(json.load(sys.stdin).get("probe_key",""))')"
echo "Fresh probe key: $PK"
# Probe-allowed tool should pass through (not -32001)
# Claimed-only tool from probe should return -32001
```

---

## 8. Rollback Plan

If 7d fails or production traffic shows >5% 5xx rate within 15 min of cutover:

### 8a. Revert nginx (immediate, restores prior /mcp path)

```bash
sudo sed -i 's|proxy_pass http://127.0.0.1:8000;|proxy_pass http://127.0.0.1:8002;|' /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl start moltrust-mcp-http.service  # in case 7c stopped it
sudo systemctl enable moltrust-mcp-http.service
# Verify /mcp now routes to legacy 8002
curl -s -X POST https://api.moltrust.ch/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"rb","version":"0.1"}}}' \
  | head -5
```

### 8b. Revert FastAPI code (if mount itself misbehaves)

The mount changes are isolated to `app/main.py` lines 50–95 and the lifespan hooks. The clean revert path is per-commit, NOT a wholesale branch revert (other commits like the security fixes should stay).

```bash
# Soft-revert just the mount block — keeps the security fixes
cd ~/moltstack
git revert --no-commit aff9f09 3dbbf51 b317b64
# This will conflict on app/main.py because later commits touched the same area.
# Resolve by keeping the security-fix lines and removing only the mount/dispatch lines.
# Better: cherry-pick a forward-fix on the running branch rather than reverting the
# mount, because the standalone mcp_http.py is still functional and serves /mcp via nginx.
```

The pragmatic rollback is 8a alone — flip nginx back to 8002, keep the FastAPI code as-is. The FastAPI app stays running with the mount in place but no external traffic reaches it via /mcp. Zero data loss.

### 8c. Decision tree

```
production 5xx > 5% after cutover?
├── YES → 8a (nginx revert) immediately
│         └── still bad?
│             └── 8b (per-commit code revert)
└── NO  → monitor for 1 hour, then proceed with rest of Phase 9
```

---

## 9. What This Report Is NOT

- A deploy authorization. Phase 9 is a manual decision by Lars based on this report and the post-cutover smoke-test results.
- A statement that the system is finding-free. The Backlog (Section 6) enumerates 6 known items that are deliberately not in scope.
- A claim that Pass 2 is the final review. If Phase-9 cutover produces unexpected behavior, run another review on the affected diff.

---

## 10. Sprint commit chain (HEAD: `9f9212b`)

```
9f9212b fix(security): hard 429 + Retry-After on probe call-cap DB failure
b317b64 fix(security): pin MCP transport-security hosts to explicit ports
0ae5ad2 fix(security): cap MCP request body at 1 MB to block OOM amplification
ccef7cd chore(deps): pin direct dependencies + document nginx real-ip policy
43d875f fix(security): IDNA-encode domain in email before hashing for claim
619f12a fix(security): enforce probe TTL+cap atomically via UPDATE ... WHERE
2104f58 fix(security): lock probe row with SELECT FOR UPDATE during claim
996c174 fix(security): stop emitting probe key as response header
e89dcbe fix(security): bucket IPv6 to /64 for rate-limit keys
2887476 test(auth): update XFF precedence test to assert secure behavior
9be1da4 chore(api): cap public list endpoints at 100 rows
3402839 fix(ops): register SlowAPIMiddleware so the configured limiter actually enforces
27bd97d feat(rbac): migrate remaining admin endpoints to verify_admin()
7ef34f0 test(identity): cleanup fixture also clears by IP, not only UA
88956b7 fix(security): use last-hop X-Forwarded-For in identity._client_ip
3dbbf51 feat(mcp): dispatch-level auth gate with per-tool identity matrix
aff9f09 feat(mcp): mount FastMCP as ASGI sub-app under FastAPI at /mcp
```

17 commits, all signed under `Lars Kroehl <kersten.kroehl@cryptokri.ch>`, none pushed yet. `git log --oneline main..HEAD` returns this list when run on `feature/auto-probe-token`.
