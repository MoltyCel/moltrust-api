# Auto-Probe-Token — Zero-Friction Onboarding Spec

**Status:** Approved for build · **Owner:** Lars + Harald · **Last updated:** 2026-05-11
**Sprint:** Active. Earliest start after Smithery listing live (today). Target: 10 dev days end-to-end including vertical integration.
**Linked context:** Smithery listing live with 39 tools, 22 weekly tool calls. MolTrust signup form on landing converted 0 to date. 7 verticals each with own onboarding pattern but no working REST APIs — all run through MCP server. Memory note: agent count is 63 (live `/skill/trust-score` lookups confirm), NOT 600.

---

## 1. Premise

Smithery is finally generating discovery. If we route discovered users through the existing email-form gate, we lose them at the same point we always have — zero conversions to date proves it. The fix is not a better signup form. The fix is to remove the gate entirely on first touch and convert it into a value-anchored upgrade prompt after the user has experienced the product.

Implementation has two parts:
1. **Auto-provision a probe DID** when an MCP connection arrives without an API key
2. **Harden the auth model** so that write tools require *some* identified key (probe or claimed) instead of running open-by-default as they do today

The second part is forced by reality: the current server accepts write-tool calls without any API key (verified live: `moltrust_register` without `X-API-Key` executes if args are valid). The probe mechanism gives us a graceful path to closing this hole without breaking existing integrations.

## 2. Mechanism overview

When a client opens an MCP session without a valid `X-API-Key` header, the server:

1. Generates a probe DID and key on-the-fly: `did:moltrust:probe:<8-hex>` + `mt_probe_<24-hex>`
2. Stores the probe DID in a separate `probe_agents` table (NOT in `agents`)
3. Returns the probe identity to the client as a server notification on the next tool call
4. From this point forward, every tool call from this session is scoped to the probe DID
5. Probe DIDs have TTL 24h, max 50 tool calls, can register up to 1 child agent, can read freely, can rate (counts toward stats only, not trust graph), cannot transfer credits, cannot claim deposits, cannot issue VCs to others, cannot interact on-chain
6. The probe key can be **claimed** at any time before TTL: `POST /auth/claim {"probe_key": "mt_probe_...", "email": "..."}` migrates the probe DID into a permanent agent record and preserves all accumulated history

The crucial properties:

| Property | Why it matters |
|---|---|
| Probes are real DIDs with full identity surface | Agent immediately experiences the product, not a sandbox |
| Probes accumulate real history (ratings made, credentials received) | Claim trigger is "keep what you built", not "create something new" |
| Probes are explicitly namespaced `did:moltrust:probe:*` and not on-chain | Cannot pollute the production trust graph, cannot be confused with real DIDs |
| Probes have hard limits (TTL, call cap, no on-chain writes) | Sybil farms can't free-ride to scale |
| Email is requested only at claim, not at entry | Removes the established conversion-killer from first touch |

## 3. Probe DID lifecycle

```
[anonymous connection]
        ↓
[server detects no X-API-Key]
        ↓
[auto-mint probe DID + key]
        ↓
[notify client: "your probe identity is X, expires Y, claim with Z"]
        ↓
        ├──→ [agent uses tools normally, accumulates state]
        │           ↓
        │    [reaches TTL or call-cap]
        │           ↓
        │    [next tool call fails with structured error: "probe expired, claim to keep, signup fresh, or proceed read-only"]
        │
        └──→ [agent calls /auth/claim with email]
                    ↓
        [server migrates probe record → permanent agent in agents table]
                    ↓
        [returns mt_<real-key>, DID rewritten to did:moltrust:<8-hex>]
                    ↓
        [history (ratings, credentials, calls) preserved]
                    ↓
        [agent continues with claimed identity, all on-chain features unlock]
```

States:

| State | Duration | Tool surface |
|---|---|---|
| `unclaimed` | 0 → 24h or 50 calls | read all, rate (recorded but not graph-effective), register 1 child agent, no on-chain |
| `claimed` | permanent | full surface (whatever the user's tier allows) |
| `expired` | 24h → 7d | read-only, can still claim if within grace |
| `gc-deleted` | after 7d | unrecoverable |

## 4. Server-side implementation

### 4.1 New DB tables

```sql
-- Probe agents are kept fully separate so they cannot leak into trust queries by accident
CREATE TABLE probe_agents (
  did              text PRIMARY KEY,                    -- did:moltrust:probe:8hex
  probe_key_hash   text NOT NULL UNIQUE,                -- sha256(mt_probe_...), key itself never stored
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,                -- now + 24h
  call_count       int NOT NULL DEFAULT 0,
  call_cap         int NOT NULL DEFAULT 50,
  first_seen_ip    inet,
  first_seen_ua    text,
  smithery_session text,                                -- if set, this came from a Smithery proxy
  claimed_at       timestamptz,                         -- null until claim
  claimed_did      text,                                -- the permanent DID after claim
  claimed_email_hash text                               -- sha256(lowercased email), for dedup
);

CREATE INDEX idx_probe_active     ON probe_agents (expires_at) WHERE claimed_at IS NULL;
CREATE INDEX idx_probe_ip_recent  ON probe_agents (first_seen_ip, created_at);

-- Probe activity is stored separately so it can be replayed during claim without touching production tables
CREATE TABLE probe_activity (
  id            bigserial PRIMARY KEY,
  probe_did     text NOT NULL REFERENCES probe_agents(did) ON DELETE CASCADE,
  tool_name     text NOT NULL,
  args_redacted jsonb,                                  -- args with PII removed
  result_summary text,
  at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_probe_act_did    ON probe_activity (probe_did, at);
```

### 4.2 New auth middleware

```python
# server.py - new pre-tool-call middleware
def resolve_identity(request) -> Identity:
    api_key = request.headers.get("X-API-Key")

    if api_key:
        # Existing path: look up in `agents` table
        agent = agents_db.lookup_by_key(api_key)
        if agent:
            return Identity(kind="claimed", did=agent.did, agent=agent)
        # Probe key (mt_probe_*) is also a valid key for probe identities
        if api_key.startswith("mt_probe_"):
            probe = probe_db.lookup_by_key(api_key)
            if probe and not probe.is_expired():
                return Identity(kind="probe", did=probe.did, probe=probe)
        # Invalid key → reject
        raise AuthError("Invalid or expired API key")

    # No key supplied: auto-mint a probe identity
    probe = probe_db.create_new(
        ip=request.client_ip,
        ua=request.user_agent,
        smithery_session=request.headers.get("Mcp-Session-Id")
    )
    return Identity(kind="probe-new", did=probe.did, probe=probe, probe_key=probe.full_key)
```

### 4.3 Tool authorization matrix

Each tool gets a `min_identity` decorator. The middleware checks it against the resolved identity.

```python
@tool(min_identity="any")           # anyone, including probes
def moltrust_stats(...): ...

@tool(min_identity="probe")         # probe or higher, must be identified
def moltrust_register(...): ...

@tool(min_identity="claimed")       # only claimed identities, on-chain
def moltrust_claim_deposit(...): ...
```

Full matrix in section 6.

### 4.4 Probe-key emission

When a fresh probe is minted, the server must communicate the key back to the agent. Three mechanisms layered for compatibility with different MCP clients:

1. **Server-initiated notification** (MCP-spec compliant): emit `notifications/message` with the probe identity payload
2. **First-tool-call result prefix**: prepend a structured note to the first tool result of the session
3. **`moltrust_identity` tool**: new no-arg read tool that returns the current probe identity at any time

The third is the most robust — Smithery and most clients don't surface server notifications well in their UIs.

```json
// moltrust_identity result for a fresh probe session
{
  "did": "did:moltrust:probe:a3f1c8e2",
  "kind": "probe",
  "key": "mt_probe_a3f1c8e2b0d49f7e8c5a1d2b3c4e5f60",
  "expires_at": "2026-05-12T11:00:00Z",
  "calls_remaining": 47,
  "claim_with": "POST https://api.moltrust.ch/auth/claim {\"probe_key\": \"...\", \"email\": \"...\"}",
  "claim_value": "Your probe has accumulated 3 ratings and 1 credential. Claim now to keep this history attached to your permanent DID."
}
```

The `claim_value` line is the conversion lever. It's dynamic: empty for a probe that has done nothing, increasingly specific the more the probe has accumulated. By the time a probe is 80% through its call cap, it's been doing real work — claim resistance approaches zero.

### 4.5 New endpoints

```
POST /auth/claim
  body: { "probe_key": "mt_probe_...", "email": "..." }
  → 200 { "did": "did:moltrust:...", "api_key": "mt_..." }
  → 409 { "error": "email_already_registered", "hint": "use existing key" }
  → 410 { "error": "probe_expired" }
  → 429 { "error": "claim_rate_limit" }

GET /probe/{probe_did}/summary
  (server-side only, used by claim flow)
  → { "ratings_made": int, "credentials_received": int, "tool_calls": int, ... }

POST /auth/claim/anonymous       [new, no email required]
  body: { "probe_key": "mt_probe_..." }
  → 200 { "did": "did:moltrust:...", "api_key": "mt_...", "tier": "anonymous_claimed" }
  Notes: creates a permanent DID with no email. Lower trust ceiling (cannot issue VCs to others), but persistent identity for agents that genuinely do not have an email contact. Rate-limited to 1 per IP per 24h.
```

The anonymous-claim path is critical. Agent-builders and autonomous agents may legitimately have no email to attach. Forcing email at claim re-creates the same gate we're trying to remove. Anonymous-claimed DIDs have a deliberate trust ceiling that motivates upgrade to email-bound when the agent matures.

## 5. Smithery integration

Three changes to the listing:

1. **Description update**: current claim ("Without it your agent is anonymous and read-only") becomes false once probe ships. New description: `"Connect with no key — your agent gets a probe DID instantly. Try every tool. Claim to make it permanent."`
2. **api_key field**: stays as-is. Empty value → server auto-mints probe.
3. **New tool surface**: `moltrust_identity` must be in the tool list so Smithery clients can show "who am I" naturally.

Smithery's `Mcp-Session-Id` header is captured and stored on the `probe_agents` row. This lets us attribute conversions specifically to Smithery in the analytics layer.

## 6. Tool authorization matrix

This is the inventory pass that hardens auth across all 39 tools. Categorize each into one of three buckets.

### 6.1 `min_identity = any` (no auth)

Public reads. Anyone, no probe needed.
- `moltrust_stats`
- `moltguard_market` (public market list)
- `moltguard_feed` (public anomaly feed, may be rate-limited per-IP)

### 6.2 `min_identity = probe`

Default for most tools. Requires *some* identified key (probe acceptable). This is the bulk.

**Core (moltrust_*):**
- `moltrust_register` — probe can register, but registered agent inherits probe status until claim
- `moltrust_verify` — any DID can be verified, probe OK
- `moltrust_reputation` — read reputation, probe OK
- `moltrust_rate` — probe ratings count but are flagged in trust graph (see section 8)
- `moltrust_credential` — probe can issue self-VC, NOT VCs to other agents
- `moltrust_credits` (balance check) — probe OK for own balance
- `moltrust_deposit_info` — probe OK to read deposit instructions
- `moltrust_deposit_history` — probe OK for own history
- `moltrust_erc8004` — read on-chain registry, probe OK

**Guard (moltguard_*):**
- `moltguard_score`, `moltguard_detail`, `moltguard_sybil`, `moltguard_credential_verify` — probe OK
- `moltguard_credential_issue` — probe can issue self-credentials only

**Verticals (mt_*):**
- `mt_shopping_info`, `mt_shopping_verify` — probe OK
- `mt_travel_info`, `mt_travel_verify` — probe OK
- `mt_skill_audit`, `mt_skill_verify` — probe OK (matches current free-tool behavior)
- `mt_prediction_link`, `mt_prediction_wallet`, `mt_prediction_leaderboard` — probe OK for reads, wallet-linking creates a probe-linked record
- `mt_salesguard_register`, `mt_salesguard_verify`, `mt_salesguard_reseller` — probe OK for register/verify; reseller flow may require claimed
- `mt_fantasy_commit`, `mt_fantasy_verify`, `mt_fantasy_history` — probe OK
- `mt_endorse_agent` — probe endorsements count but are flagged
- `mt_create_interaction_proof` — probe OK
- `mt_get_trust_score` — probe OK

### 6.3 `min_identity = claimed`

Touches money or production trust graph permanently. Must be claimed.

- `moltrust_credits` (transfer subcommand only) — moving USDC requires claimed
- `moltrust_claim_deposit` — claiming USDC deposit requires claimed
- `mt_shopping_issue_vc`, `mt_travel_issue_vc`, `mt_skill_issue_vc` — issuing VCs to *other* agents (not self) requires claimed

This list deliberately small. The goal is "let probes do everything that doesn't permanently affect production state".

## 7. Vertical integration

The seven vertical landing pages currently each pitch a use-case and dead-end. Probe DIDs let them all share a single, consistent CTA path.

### 7.1 Unified CTA pattern

Every vertical landing page changes its primary CTA from current scattered approaches to one shape:

```
[ Try with a probe agent (no signup) ]   →  spawns an in-page MCP-over-WebSocket session
[ Claim to keep your work ]              →  shown after first successful tool execution
```

The "Try with a probe" button connects via a browser-side MCP client (we ship a small JS lib at `moltrust.ch/probe.js`) that:
1. Opens an MCP session to `api.moltrust.ch/mcp` with no key
2. Calls `moltrust_identity` to surface the probe DID to the visitor
3. Pre-fills the vertical-specific tool call form (e.g. for `/skills.html` it pre-fills `mt_skill_audit`)
4. Executes the tool on user submit, renders result inline
5. After 1–2 successful interactions, renders a sticky claim banner

### 7.2 Per-vertical landing changes

| Vertical page | Current CTA | New primary CTA | Probe-driven demo tool |
|---|---|---|---|
| `/sports.html` | "Get API Key →" | "Try the verifier" | `moltrust_verify` |
| `/regulated-markets.html` | "Quick Start"/Doku | "Audit a market" | `moltguard_market` + `moltguard_detail` |
| `/prediction.html` | "Link Your Wallet" | "Spot anomalies live" | `moltguard_feed` |
| `/skills.html` | "Audit a Skill (Free)" | unchanged (already works), add claim banner | `mt_skill_audit` |
| `/shopping.html` | "Shopping API Info →" | "Verify a merchant" | `mt_shopping_verify` |
| `/travel.html` | "Issue Travel Agent VC" | "Try issuing (self-VC)" | `mt_travel_issue_vc` (self-target) |
| `/salesguard.html` | "Request Access" | "Verify a reseller" | `mt_salesguard_verify` |
| `/developers.html` | doc-only | add "Spawn probe" snippet at top | `moltrust_identity` |

### 7.3 Cross-vertical persistence

Visitor lands on `/shopping.html`, spawns probe X, runs a verify. Then navigates to `/travel.html`. The probe.js library persists probe key in localStorage and reuses it — so the visitor doesn't get a fresh probe per page. By the time they hit the claim prompt, their probe has accumulated activity across multiple verticals, making claim-value pitch concrete: *"Your probe agent has used 4 verticals (skills, shopping, travel, prediction) and earned 2 self-credentials. Claim to keep this identity."*

This is the real conversion engine. Single-vertical exposure rarely converts; cross-vertical exposure inside one probe session demonstrates the breadth of the platform.

## 8. Sybil mitigations

| Vector | Mitigation |
|---|---|
| Probe-farm: bot creates 10k probe DIDs to manipulate stats | Probe ratings flagged `probe=true` in trust graph queries; default trust score query excludes probe ratings entirely |
| IP-based probe spamming | Rate limit: 5 fresh probes per IP per hour, 20 per /24 subnet per hour |
| Browser-fingerprint probe rotation | UA + IP combined for soft cap, hard cap on /24 |
| Claim-spam: bot claims many probes via disposable emails | Email-domain blocklist (mailinator, etc.), claim rate limit 3 per IP per day, anonymous-claim rate limit 1 per IP per day |
| Reputation-wash: bad DID → fresh probe → claim → reset | Email hash check at claim — if email previously claimed, return existing DID instead of creating new (idempotent claim); anonymous claims don't have this protection by design but have lower trust ceiling |
| Probe-credit mining | Probes cannot earn credits; the credit-balance check returns 0 for probe DIDs; no probe → claimed credit migration |
| Garbage accumulation | Auto-GC cron daily 04:00 UTC: drop probe_agents and probe_activity older than 7 days from creation if not claimed |
| Probe used as on-chain identity | Probe DIDs are NEVER written to ERC-8004 registry; the `mt_erc8004_register` write subcommand checks identity kind and rejects probes with clear error |

## 9. Conversion analytics

Tracking required to validate the >10% probe→claim hypothesis. New analytics dimension:

```sql
CREATE TABLE conversion_funnel (
  probe_did    text PRIMARY KEY REFERENCES probe_agents(did),
  source       text,                    -- 'smithery' | 'landing-skills' | 'landing-travel' | ...
  first_tool   text,                    -- which tool was the entry
  tool_count   int,                     -- total tools called by this probe
  unique_tools int,                     -- distinct tool names
  verticals_touched int,                -- distinct vertical prefixes (moltrust, moltguard, mt_*)
  claim_state  text,                    -- 'unclaimed' | 'claimed' | 'expired' | 'anonymous-claimed'
  claimed_at   timestamptz
);
```

Reports run daily:
- Smithery probe spawn rate (target: rising)
- Probe → first tool call rate (target: >50%)
- Probe → 3+ tool calls rate (target: >30%)
- Probe → claim rate, by source (target: >10% from Smithery, >5% from landings)
- Average verticals touched per claimed probe (signal of cross-vertical persistence working)
- Time from first tool call to claim (target: 80% within 24h)

A weekly digest report goes to Lars's email summarizing these. If after 30 days probe→claim rate is below 5% across all sources, the spec hypothesis is disproven and we go back to pain-pull / discovery work.

## 10. Edge cases

1. **Probe runs out mid-session**: next tool call returns structured error with three options — claim, signup fresh, or proceed read-only with stats tool only. Smithery clients can present this as a UI prompt; programmatic agents handle it programmatically.
2. **Smithery long-lived session vs short probe TTL**: Smithery may hold a session open for hours/days. The probe TTL is the source of truth — when it expires, the connection effectively degrades. To smooth this, if a probe is *active* (recent tool call) when 80% through its TTL, auto-extend by 12h, max 2 extensions. Beyond that, must claim.
3. **Same agent reconnects with same probe key**: legitimate. Server recognizes returning probe, resumes state, does not re-mint.
4. **Probe attempts a claimed-only tool**: returns structured error pointing at `POST /auth/claim`, NOT a generic 403. The error includes the exact curl to claim.
5. **Email-claim collision**: existing email tries to claim a new probe. Server returns the existing DID + key (not a duplicate). Optional: migrate probe history into existing DID (off by default — could be exploited; opt-in flag in claim body `migrate_probe_history: true`).
6. **Two probes with same IP signature collide at claim**: shouldn't happen if rate limits hold, but if it does, the second claim attempt returns 409 with a message about IP-claim limits.
7. **Production agent uses probe.js by mistake**: probe.js detects existing `mt_` (non-probe) key in localStorage and uses that; never overwrites a real key with a probe.
8. **MoltGuard Triggers spec lands later**: probe DIDs CANNOT subscribe to triggers (delivery URL would point at ephemeral webhook receiver). Triggers require claimed identity. Documented in MoltGuard spec section 4.

## 11. Implementation phases

**Phase 1 — Server core (4 dev days, Harald primary):**
- DB migration: `probe_agents`, `probe_activity`, `conversion_funnel`
- Auth middleware rewrite with identity resolution
- Tool authorization decorators applied across all 39 tools
- `moltrust_identity` tool implemented
- `/auth/claim` and `/auth/claim/anonymous` endpoints
- Auto-mint logic with TTL, call cap, GC cron
- Unit tests for the matrix

**Phase 2 — Smithery polish (0.5 day, Lars):**
- Update Smithery listing description
- Verify `moltrust_identity` shows in Smithery's tool view after re-publish
- Smoke test: connect to Smithery gateway with empty `api_key`, confirm probe spawn

**Phase 3 — probe.js library (2 dev days, Lars):**
- Lightweight MCP client in browser-compatible JS
- LocalStorage probe-key persistence
- Pre-built widget components for each vertical
- CDN distribution at `moltrust.ch/probe.js`

**Phase 4 — Vertical landing rewrites (2 dev days, Lars):**
- Update 7 landing pages per matrix in 7.2
- Sticky claim banner component
- Cross-vertical probe persistence verification

**Phase 5 — Analytics + monitoring (1 dev day, Harald or Lars):**
- `conversion_funnel` writes integrated into tool dispatch
- Weekly digest cron
- Grafana board with funnel metrics

**Total: 9.5 dev days.** Realistic with buffer: 12 working days.

## 12. Migration plan for existing identities

The 63 existing agents in the production DB are unaffected. They have real DIDs and real keys. The probe table is new and additive. No data migration required.

The `mt_test_key_2026` public test key remains valid as a normal claimed key — not migrated to probe. Recommendation: at Phase 1 deploy, mark it as a "test fixture" key with a special analytics tag so its noise can be filtered from real-agent metrics. This is preparation for eventual rotation but doesn't break documentation that references the key.

## 13. Open questions

- **Probe ratings effect on trust graph**: spec says probe ratings are stored but excluded from default trust queries. Should probe ratings *ever* count, e.g. with low weight? Probably not in MVP, revisit if data shows interesting signal.
- **Probe credentials**: probes can self-issue VCs but those VCs presumably have low verification value. Should the VC payload include `issuer_kind: probe` for transparency? Yes, recommend yes.
- **Email-only claim vs anonymous claim ratio**: we'll measure this. If anonymous claims dominate, that's a signal that the email field is still friction even at the claim stage — could lead to a future iteration where email becomes truly optional at first claim and only required for VC-issuance.
- **MoltGuard Triggers interaction**: when triggers ship, claimed-identity gate is hardcoded. Should specific MoltGuard event types (e.g. `market.anomaly.detected`) be probeable, given the public anomaly feed already is? Possibly with hard rate limits. Defer to MoltGuard spec revision.
- **Smithery-attributed probes vs other sources**: are we comfortable having `smithery_session` stored unencrypted on the probe row? It's not PII per se, but it does identify the proxy. Recommendation: store hashed, not raw.
- **Probe-to-claimed credential migration**: spec says self-issued VCs stay with the probe through claim (the DID is renamed, credentials follow). On-chain anchoring of those VCs at claim time — yes or no? Default no; user opts in via flag in claim request.

## 14. Success criteria

After 30 days post-Phase-5 deploy:
- ≥100 fresh probe DIDs spawned (low bar — anything below means discovery is the real problem)
- ≥30 probes execute ≥3 tool calls (engagement threshold)
- ≥10 probes claim, of which ≥5 via Smithery source (conversion threshold)
- Trust graph integrity unaffected (no probe DID accidentally counted in production trust score for any non-probe agent)

If success criteria are met, expand to:
- Probe spawn from MoltGuard Triggers spec (when that ships)
- Browser-extension version of probe.js for agent-builder workflows
- Affiliate model where probe.js can be embedded by third parties with attribution
