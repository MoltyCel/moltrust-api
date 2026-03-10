"""MolTrust Agent Watchdog - Monitors all cron agents and alerts on failure."""

import os, sys, json, datetime, glob, httpx, logging

DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("watchdog")

# Agent definitions: name, max_hours without activity, check method
AGENTS = [
    {
        "name": "Herald",
        "heartbeat_file": os.path.join(DATA_DIR, "herald_heartbeat.json"),
        "max_hours": 8,  # runs 4x/day = every 6h, give 2h grace
        "fallback_glob": "herald_*.md",
    },
    {
        "name": "Scout",
        "heartbeat_file": None,
        "max_hours": 14,  # runs 2x/day = every 12h
        "fallback_glob": "scout_*.md",
    },
    {
        "name": "Ambassador",
        "heartbeat_file": None,
        "max_hours": 1.5,  # runs every 30min, give 1.5h grace
        "fallback_log": "ambassador.log",
    },
    {
        "name": "Moltbook Poster",
        "heartbeat_file": os.path.join(DATA_DIR, "moltbook_state.json"),
        "heartbeat_ts_key": "last_post_time",  # uses different key than "timestamp"
        "max_hours": 14,  # runs 2x/day = every 12h, give 2h grace
        "fallback_glob": "moltbook_*.md",
    },
]


def send_telegram(message: str) -> bool:
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

    if alerts:
        msg = "🐕 <b>Watchdog Alert</b>\n\n" + "\n".join(alerts)
        log.warning(f"Sending alert for {len(alerts)} agent(s)")
        send_telegram(msg)
    else:
        log.info("All agents healthy")


if __name__ == "__main__":
    run()
