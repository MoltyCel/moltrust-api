"""MolTrust Agent Watchdog - Monitors all cron agents and alerts on failure."""

import os, sys, json, datetime, glob, httpx, logging

from app import notify

DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Discovery-surface reconciliation ---------------------------------------
# Two surfaces agents discover us through: the MCP tool catalog (Smithery
# listing) and the A2A Agent-Card. When the origin gains/loses a tool but the
# Smithery listing hasn't re-scanned, discovery goes stale SILENTLY. With Option
# B (Smithery lists remote-at-origin, api.moltrust.ch/mcp), the steady state is
# origin == listing → Δ0. Any Δ = the Smithery remote needs a re-scan. This
# reconciles what we serve against each listing — "did the listing keep up", not
# "did a run error". The Smithery registry is queryable (registry.smithery.ai).
MCP_LOCAL_URL = "http://127.0.0.1:8002/mcp"
SMITHERY_REGISTRY_URL = "https://registry.smithery.ai/servers/@moltrust/moltrust-mcp-server"
AGENT_CARD_URL = "https://api.moltrust.ch/.well-known/agent-card.json"
# The Agent-Card has no independent live source-of-truth for "expected skills",
# so this is a pinned counter — BUMP IT when you add/remove a skill (see the
# Discovery-Checklist in CLAUDE.md). Mismatch => card regressed OR baseline stale.
EXPECTED_AGENT_CARD_SKILLS = 13

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("watchdog")

# Agent definitions: name, max_hours without activity, check method
# Moltbook Poster: DISABLED 2026-03-30 — Moltbook API down post Meta acquisition (500 errors since 2026-03-27)
AGENTS = [
    {
        "name": "Herald",
        "heartbeat_file": os.path.join(DATA_DIR, "herald_heartbeat.json"),
        "max_hours": 12,  # runs 4x/day = every 6h, give 2h grace
        "fallback_glob": "herald_*.md",
    },
    {
        "name": "Scout",
        "heartbeat_file": None,
        "max_hours": 15,  # runs 2x/day = every 12h, 3h grace
        "fallback_glob": "scout_*.md",
    },
    {
        "name": "Ambassador",
        "heartbeat_file": None,
        "max_hours": 1.5,  # runs every 30min, give 1.5h grace
        "fallback_log": "ambassador.log",
    },
    {
        "name": "News Scout",
        "heartbeat_file": os.path.join(DATA_DIR, "news_scout_heartbeat.json"),
        "max_hours": 26,  # runs 1x/day, give 2h grace
        "fallback_glob": None,
    },
    # REMOVED 2026-05-13 — TrustScout-Monitoring via state-file-field
    # ist strukturell unzuverlaessig (multi-writer trustscout.py + moltguard.py
    # schreiben dasselbe File, last_post_time kann stale werden ohne dass Posts
    # ausfallen). Diagnose 13.05.26: Posts auf Moltbook funktionieren weiter
    # (verifiziert via MolTrust Telegram Stats), State-File-Update ist nicht
    # zuverlaessiges Health-Signal. Falls zukuenftig TrustScout-Health-Check
    # gewuenscht: separater Check direkt gegen Moltbook-Post-Resultat (nicht
    # state-file). Bis dahin: kein Watchdog-Eintrag.
]


def send_telegram(message: str) -> bool:
    if not notify.telegram_allowed("watchdog.send_telegram", logger=log):
        return False
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


def check_heartbeat(agent: dict, now: datetime.datetime) -> dict:
    """Check agent health. Returns {ok: bool, detail: str}."""
    name = agent["name"]

    # Method 1: heartbeat JSON file
    hb_file = agent.get("heartbeat_file")
    if hb_file and os.path.exists(hb_file):
        try:
            with open(hb_file) as f:
                hb = json.load(f)
            ts_key = agent.get("heartbeat_ts_key", "timestamp")
            ts = datetime.datetime.fromisoformat(hb[ts_key])
            age_h = (now - ts).total_seconds() / 3600
            status = hb.get("status", "unknown")
            if age_h > agent["max_hours"]:
                return {"ok": False, "detail": f"Last heartbeat {age_h:.1f}h ago (max {agent['max_hours']}h), status={status}"}
            if status in ("crash", "error"):
                return {"ok": False, "detail": f"Heartbeat status={status}: {hb.get('detail', '')[:200]}"}
            return {"ok": True, "detail": f"Heartbeat {age_h:.1f}h ago, status={status}"}
        except Exception as e:
            return {"ok": False, "detail": f"Heartbeat file unreadable: {e}"}

    # Method 2: check latest glob file
    fallback_glob = agent.get("fallback_glob")
    if fallback_glob:
        files = sorted(glob.glob(os.path.join(LOG_DIR, fallback_glob)))
        if not files:
            return {"ok": False, "detail": "No output files found"}
        latest = files[-1]
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(latest), tz=datetime.UTC)
        age_h = (now - mtime).total_seconds() / 3600
        if age_h > agent["max_hours"]:
            return {"ok": False, "detail": f"Latest file {age_h:.1f}h old (max {agent['max_hours']}h): {os.path.basename(latest)}"}
        return {"ok": True, "detail": f"Latest file {age_h:.1f}h ago: {os.path.basename(latest)}"}

    # Method 3: check log file mtime
    fallback_log = agent.get("fallback_log")
    if fallback_log:
        log_path = os.path.join(LOG_DIR, fallback_log)
        if not os.path.exists(log_path):
            return {"ok": False, "detail": f"Log file missing: {fallback_log}"}
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(log_path), tz=datetime.UTC)
        age_h = (now - mtime).total_seconds() / 3600
        if age_h > agent["max_hours"]:
            return {"ok": False, "detail": f"Log stale: {age_h:.1f}h old (max {agent['max_hours']}h)"}
        return {"ok": True, "detail": f"Log updated {age_h:.1f}h ago"}

    return {"ok": False, "detail": "No check method configured"}



def _live_mcp_tool_count() -> "int | None":
    """tools/list from the running MCP server (local :8002, no auth needed)."""
    import asyncio
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async def _q() -> int:
        async with streamablehttp_client(MCP_LOCAL_URL) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return len((await s.list_tools()).tools)

    try:
        return asyncio.run(_q())
    except Exception:
        return None


def check_discovery_drift(now: datetime.datetime) -> list:
    """Reconcile the two discovery surfaces against what we actually serve.
    Returns a list of {surface, ok, detail}."""
    out = []
    # 1) MCP tool catalog: running server vs Smithery listing.
    live = _live_mcp_tool_count()
    if live is None:
        out.append({"surface": "MCP", "ok": False,
                    "detail": "tools/list unreachable (mcp_http :8002 down?)"})
    else:
        try:
            # cache-bust: registry.smithery.ai sits behind Cloudflare (max-age 4h,
            # stale-while-revalidate 24h) — the plain URL can lag a real change by
            # hours (verified 2026-07-18: cached 39 vs fresh 53). Force a fresh
            # read so a legit re-scan doesn't trigger a day of false drift alarms.
            sm = httpx.get(SMITHERY_REGISTRY_URL, params={"_cb": int(now.timestamp())},
                           headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
                           timeout=12.0).json()
            listed = len(sm.get("tools") or [])
            # Steady state (Option B: Smithery lists remote-at-origin) is
            # origin == listing → Δ0 → silent. Any Δ is real drift: the origin
            # gained/lost a tool and the Smithery remote hasn't re-scanned.
            if live != listed:
                out.append({"surface": "MCP↔Smithery", "ok": False,
                            "detail": f"origin exposes {live} tools, Smithery lists {listed} "
                                      f"(Δ{live - listed}) — Smithery remote out of sync with the "
                                      f"origin; re-scan/redeploy the Smithery listing"})
            else:
                out.append({"surface": "MCP↔Smithery", "ok": True,
                            "detail": f"{live} tools in sync (origin == listing)"})
        except Exception as e:
            # A Smithery registry outage must not masquerade as our drift.
            out.append({"surface": "MCP↔Smithery", "ok": True,
                        "detail": f"Smithery registry unreachable ({type(e).__name__}), skipped"})
    # 2) A2A Agent-Card skills vs pinned baseline.
    try:
        card = httpx.get(AGENT_CARD_URL, timeout=10.0).json()
        skills = card.get("skills") or card.get("capabilities") or []
        n = len(skills) if isinstance(skills, list) else 0
        if n != EXPECTED_AGENT_CARD_SKILLS:
            out.append({"surface": "Agent-Card", "ok": False,
                        "detail": f"card exposes {n} skills, baseline {EXPECTED_AGENT_CARD_SKILLS} "
                                  f"— update the card or bump EXPECTED_AGENT_CARD_SKILLS"})
        else:
            out.append({"surface": "Agent-Card", "ok": True,
                        "detail": f"{n} skills == baseline"})
    except Exception as e:
        out.append({"surface": "Agent-Card", "ok": False,
                    "detail": f"agent-card fetch failed: {type(e).__name__}"})
    return out


def check_conformance_drift() -> dict:
    """Check if CONFORMANCE.md files match live API checksum."""
    import subprocess
    try:
        result = subprocess.run(
            ["/home/moltstack/moltguard/scripts/check_drift.sh"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return {"ok": True, "detail": "CONFORMANCE.md in sync with API"}
        elif result.returncode == 1:
            # Extract drift details from output
            lines = [l for l in result.stdout.strip().split("\n") if "DRIFT" in l or "Missing" in l]
            detail = "; ".join(lines[:3]) if lines else "Drift detected"
            return {"ok": False, "detail": detail}
        else:
            return {"ok": False, "detail": f"API unreachable (exit {result.returncode})"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "Drift check timed out (15s)"}
    except Exception as e:
        return {"ok": False, "detail": f"Drift check error: {e}"}


def run():
    now = datetime.datetime.now(datetime.UTC)
    log.info(f"Watchdog run at {now.strftime('%Y-%m-%d %H:%M UTC')}")

    alerts = []
    for agent in AGENTS:
        result = check_heartbeat(agent, now)
        status = "✅" if result["ok"] else "❌"
        log.info(f"  {status} {agent['name']}: {result['detail']}")
        if not result["ok"]:
            alerts.append(f"❌ <b>{agent['name']}</b>: {result['detail']}")

    # CONFORMANCE.md drift check
    drift = check_conformance_drift()
    status = "✅" if drift["ok"] else "❌"
    log.info(f"  {status} CONFORMANCE Drift: {drift['detail']}")
    if not drift["ok"]:
        alerts.append(f"❌ <b>CONFORMANCE Drift</b>: {drift['detail']}")

    # Discovery-surface reconciliation (MCP↔Smithery, Agent-Card)
    for r in check_discovery_drift(now):
        status = "✅" if r["ok"] else "❌"
        log.info(f"  {status} Discovery/{r['surface']}: {r['detail']}")
        if not r["ok"]:
            alerts.append(f"❌ <b>Discovery/{r['surface']}</b>: {r['detail']}")

    if alerts:
        msg = "🐕 <b>Watchdog Alert</b>\n\n" + "\n".join(alerts)
        log.warning(f"Sending alert for {len(alerts)} agent(s)")
        send_telegram(msg)
    else:
        log.info("All agents healthy")


if __name__ == "__main__":
    run()
