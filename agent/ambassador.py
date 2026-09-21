"""MolTrust Ambassador Agent — autonomous trust onboarding daemon."""
import os, sys, json, asyncio, logging, datetime

sys.path.insert(0, os.path.expanduser("~/moltstack"))

import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.credentials import issue_credential, vc_valid_from, vc_valid_until
from app.funnel import is_organic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AMBASSADOR_NAME = "MolTrust Ambassador"
AMBASSADOR_DID = "did:moltrust:ambassador0001"
CHECK_INTERVAL = 300  # 5 minutes
STATS_PORT = 8001
MILESTONE_STEP = 100

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("ambassador")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
db_pool: asyncpg.Pool = None
last_known_milestone: int = 0

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        host="localhost", database="moltstack",
        user="moltstack", password=os.getenv("MOLTSTACK_DB_PW", ""),
        min_size=2, max_size=5,
    )

async def close_db():
    if db_pool:
        await db_pool.close()

# ---------------------------------------------------------------------------
# 1. Self-registration
# ---------------------------------------------------------------------------
async def ensure_self_registered():
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT did FROM agents WHERE did = $1", AMBASSADOR_DID)
        if row:
            log.info("Ambassador already registered: %s", AMBASSADOR_DID)
            return
        await conn.execute(
            "INSERT INTO agents (did, display_name, platform, created_at) VALUES ($1, $2, $3, now())",
            AMBASSADOR_DID, AMBASSADOR_NAME, "system",
        )
        vc = issue_credential(AMBASSADOR_DID, "AgentTrustCredential", {
            "trustProvider": "MolTrust",
            "reputation": {"score": 0.0, "total_ratings": 0},
            "verified": True,
            "role": "ambassador",
        })
        await conn.execute(
            """INSERT INTO credentials (subject_did, credential_type, issuer, issued_at, expires_at, proof_value, raw_vc)
            VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            AMBASSADOR_DID, "AgentTrustCredential", vc["issuer"],
            datetime.datetime.fromisoformat(vc_valid_from(vc).replace("Z", "")),
            datetime.datetime.fromisoformat(vc_valid_until(vc).replace("Z", "")),
            vc["proof"]["proofValue"],
            json.dumps(vc),
        )
        log.info("Ambassador registered with DID %s", AMBASSADOR_DID)

# ---------------------------------------------------------------------------
# 2. Welcome new agents
# ---------------------------------------------------------------------------
async def welcome_new_agents():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.did, a.display_name
            FROM agents a
            LEFT JOIN agent_messages m ON a.did = m.to_did
            WHERE m.id IS NULL AND a.did != $1
        """, AMBASSADOR_DID)

        if not rows:
            return 0

        count = 0
        for row in rows:
            did = row["did"]
            name = row["display_name"] or "agent"
            message = (
                f"Hey {name}, welcome to MolTrust \u2014 trust infrastructure for AI agents. "
                f"Your DID {did} is live and anchored on Base. "
                f"We cover 5 verticals: agent scoring, skill verification, "
                f"prediction market integrity, shopping & travel agent credentials. "
                f"Get started: pip install moltrust | "
                f"MCP Server: pip install moltrust-mcp-server | "
                f"Docs & verticals: https://moltrust.ch"
            )
            await conn.execute(
                "INSERT INTO agent_messages (to_did, message) VALUES ($1, $2)",
                did, message,
            )
            count += 1

        if count:
            log.info("Welcomed %d new agent(s)", count)
        return count

# ---------------------------------------------------------------------------
# 3. Milestone detection + notify (autonomous X-posting disabled per §0.1)
# ---------------------------------------------------------------------------
async def check_milestones():
    """Announce a milestone only when reach produced it.

    This used to count every row in `agents` and subtract the test fixtures.
    That made the milestone a measure of the budget: 98 of the 222 agents on
    2026-09-21 came from the bounty market at roughly 0.16 USDC each, and they
    alone carried the total past 200. A suggestion to post about it would have
    invited us to announce something we bought.

    Counted now: registrations that are neither paid, internal, test, nor a
    partner's own population. Those four classes are defined once in
    `app/funnel.py` and shared with the funnel panel, so the milestone and the
    acquisition goal cannot drift apart.
    """
    global last_known_milestone
    async with db_pool.acquire() as conn:
        # registration_ip comes along because being ours is not only a
        # platform value: 36 agents across three platform strings registered
        # from one /24 of our own infrastructure. funnel_is_internal is the
        # migrated SQL twin of app.funnel.is_internal.
        rows = await conn.fetch(
            "SELECT platform, agent_type, "
            "funnel_is_internal(platform, registration_ip) AS internal, "
            "COUNT(*) AS n "
            "FROM agents GROUP BY 1, 2, 3"
        )

    def _organic(r) -> bool:
        return not r["internal"] and is_organic(r["platform"], r["agent_type"])

    total = sum(r["n"] for r in rows)
    organic = sum(r["n"] for r in rows if _organic(r))
    excluded = {}
    for r in rows:
        if _organic(r):
            continue
        key = "intern" if r["internal"] else str(r["platform"] or "unset")
        excluded[key] = excluded.get(key, 0) + r["n"]

    current_milestone = (organic // MILESTONE_STEP) * MILESTONE_STEP
    if current_milestone > last_known_milestone and last_known_milestone > 0:
        log.info("Milestone reached: %d organic agents (of %d total)",
                 current_milestone, total)
        # Autonomous X posting is disabled per WORKFLOW.md §0.1 (deactivated 2026-04-12).
        # Notify-only: no auto-tweet, no new bot token. The suggestion to draft
        # one is now safe to make, because a paid jump cannot reach this line.
        breakdown = ", ".join(f"{k} {v}" for k, v in sorted(excluded.items(),
                                                            key=lambda kv: -kv[1]))
        try:
            from agents.watchdog import send_telegram
            from app import notify
            sent = send_telegram(
                f"MolTrust Milestone: {organic} organische Registrierungen "
                f"(von {total} Agenten insgesamt).\n"
                f"Nicht gezählt: {breakdown or 'nichts'}.\n"
                f"Autonomer X-Post per §0.1 deaktiviert — manuellen Draft posten?",
                channel=notify.STATS,
            )
            if not sent:
                log.warning("milestone notify: telegram send returned False (creds unset?)")
        except Exception as e:
            log.error("milestone notify failed: %s", e)
    last_known_milestone = current_milestone

# ---------------------------------------------------------------------------
# 4. Stats dashboard endpoint
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await ensure_self_registered()
    asyncio.create_task(run_loop())
    yield
    await close_db()

stats_app = FastAPI(title="MolTrust Ambassador", version="1.0", lifespan=lifespan)

@stats_app.get("/stats")
async def stats_dashboard():
    now = datetime.datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    async with db_pool.acquire() as conn:
        active_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE last_seen > $1",
            now - datetime.timedelta(hours=24),
        )
        new_today = await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE created_at >= $1", today_start,
        )
        total_agents = await conn.fetchval("SELECT COUNT(*) FROM agents")
        mix = await conn.fetch(
            "SELECT platform, agent_type, "
            "funnel_is_internal(platform, registration_ip) AS internal, "
            "COUNT(*) AS n "
            "FROM agents GROUP BY 1, 2, 3"
        )
        total_credentials = await conn.fetchval("SELECT COUNT(*) FROM credentials")
        total_welcomed = await conn.fetchval("SELECT COUNT(*) FROM agent_messages")
    organic_agents = sum(
        r["n"] for r in mix
        if not r["internal"] and is_organic(r["platform"], r["agent_type"])
    )
    return {
        "ambassador_did": AMBASSADOR_DID,
        "timestamp": now.isoformat() + "Z",
        "active_agents_24h": active_24h,
        "new_registrations_today": new_today,
        "total_agents": total_agents,
        # The milestone counts organic registrations only, so the number this
        # endpoint reports as the next one has to be computed the same way —
        # otherwise the dashboard promises a milestone the bot will not fire.
        "organic_agents": organic_agents,
        "total_credentials": total_credentials,
        "total_welcomed": total_welcomed,
        "next_milestone": ((organic_agents // MILESTONE_STEP) + 1) * MILESTONE_STEP,
    }

@stats_app.get("/health")
async def health():
    return {"status": "ok", "agent": AMBASSADOR_NAME}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
async def run_loop():
    log.info("Ambassador loop started (interval: %ds)", CHECK_INTERVAL)
    await check_milestones()
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            await welcome_new_agents()
            await check_milestones()
        except Exception as e:
            log.error("Loop error: %s", e)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(stats_app, host="127.0.0.1", port=STATS_PORT, log_level="info")
