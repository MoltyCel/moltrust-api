# Audit 2026-06-22 — Disarm autonomous X milestone-post (legacy ambassador)

**Console action.** Read-only diagnosis → surgical disarm. §11.5 audit-eintrag (server-infra touch: systemd unit is NOT repo-managed).

## Finding

A **second legacy on Hetzner** (besides the Mac legacy disabled 2026-06-20): systemd
`moltrust-agent.service` runs `-m agent.ambassador` = the **singular** `agent/ambassador.py`
(8.5 KB, May-25, sha256 `6575b4d…`). Distinct from the maintained **plural**
`agents/ambassador.py` (cron, Moltbook engagement) — two different agents, same filename.

The singular daemon's `check_milestones()` carried an **autonomous X-post** path
(`tweepy` → `create_tweet`). Status at discovery — **armed but dormant**:

- systemd `EnvironmentFile=/home/moltstack/.moltrust_secrets` → env injected.
- All four `X_*` creds **SET** (presence only checked; no values read).
- journald: **zero** `Posted milestone tweet` ever; no X errors.
- Reason dormant: milestone gate needs ≥100 agents; **agents = 79** → `current_milestone = 0`, trigger never reached.
- Service **active + enabled** since 2026-06-11; `Welcomed …` still firing (through 2026-06-22).

## §0.1 status

**Intact — NOT an incident.** No autonomous post has ever gone out. This is **preventive
disarming of a sharp-but-dormant weapon**, not remediation of a violation. Risk basis: the
path would auto-fire at the next 100-agent boundary with valid creds, contradicting
"autonomes Bot-Posting deaktiviert seit 12.04.26".

## Action

`fix(ambassador): disarm autonomous X milestone-post → notify-only (§0.1 guarantee)`

- Replaced `create_tweet(...)` with **notify-only** via the existing `agents/watchdog.py`
  `send_telegram()` (reads `TELEGRAM_BOT_TOKEN`/`CHAT_ID` from env — **no new token**, hygiene #9).
- Removed now-orphaned `import tweepy` + `get_x_client()`. Milestone **detection +
  idempotency guard** (`last_known_milestone`) left **unchanged**.
- Notify message breaks down real vs test to avoid a misleading count:
  `agents total=N (real=X, test=Y)` — derived from `agents.platform = 'test'`
  (verified via `\d agents`; at audit: total 79, test 10, real 69).
- §0.1 is now **code-hard**, not merely config/threshold-dormant.
- NOT touched: `agents/ambassador.py` (plural/cron), and the singular file's
  `ensure_self_registered()` / `welcome_new_agents()` / `:8001` stats API.

§12 does not apply (internal server deploy; no npm-publish / non-MolTrust PR / external recipient).

## Root cause of the mis-as-duplicate diagnosis

**Naming collision** `agent/` vs `agents/`, both `ambassador.py` — two distinct agents.
Rename tracked as a separate BACKLOG item (not in this commit).

## Deploy (server-infra, §11 boundary)

Post-merge: `git -C ~/moltstack pull --ff-only origin main` (only if `agent/ambassador.py`
clean) + `sudo systemctl restart moltrust-agent`. Verify `post-sha == repo-sha`,
`is-active`, `grep create_tweet` gone, no new journald ERROR.

## Deferred (separate, not now)

- Rename singular `agent/ambassador.py` + `moltrust-agent.service` ExecStart (server-infra → own audit).
- Recurring empty `[ERROR] Loop error:` in `run_loop` — read-only diagnosis, own commit.
