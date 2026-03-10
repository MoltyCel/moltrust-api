"""MolTrust Herald Agent v2 - Auto-posts to X with monitoring & alerts"""

import os, sys, glob, datetime, json, logging, traceback, httpx, tweepy

AGENT_DID = "did:moltrust:97caa5d172314d80"
AGENT_NAME = "MolTrust Herald"
LOG_DIR = os.path.expanduser("~/moltstack/logs")
DATA_DIR = os.path.expanduser("~/moltstack/data")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "herald_heartbeat.json")
STATE_FILE = os.path.join(DATA_DIR, "herald_state.json")

# Env vars
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
X_CONSUMER_KEY = os.getenv("X_CONSUMER_KEY", "")
X_CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("herald")

os.makedirs(DATA_DIR, exist_ok=True)


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set")
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def write_heartbeat(status: str, detail: str = ""):
    """Write heartbeat file on every run — success or failure."""
    hb = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "status": status,
        "detail": detail,
    }
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump(hb, f)
    except Exception:
        pass


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def get_x_client():
    if not all([X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
        return None
    return tweepy.Client(
        consumer_key=X_CONSUMER_KEY,
        consumer_secret=X_CONSUMER_SECRET,
        access_token=X_ACCESS_TOKEN,
        access_token_secret=X_ACCESS_SECRET,
    )


def get_latest_briefing():
    files = sorted(glob.glob(os.path.join(LOG_DIR, "scout_*.md")))
    if not files:
        return None
    with open(files[-1]) as f:
        return f.read()


def generate_post(briefing: str) -> str | None:
    prompt = (
        "You are the MolTrust Herald Agent, social media manager for @moltrust on X. "
        "MolTrust is a trust layer API for the agent economy: W3C DID:web, VCs with Ed25519, "
        "reputation scoring, Lightning payments. MoltGuard is our newest product: agent trust scores, "
        "sybil detection, prediction market integrity, verifiable credentials — all free during Early Access. "
        "Based on this Scout briefing, generate exactly ONE tweet that: "
        "1. References a current trend 2. Positions MolTrust as relevant "
        "3. Is engaging and concise 4. Max 270 chars 5. Includes 1-2 hashtags. "
        "Return ONLY the tweet text, nothing else."
        "\n\nBriefing:\n" + briefing
    )
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    if not texts:
        raise RuntimeError("Anthropic returned no text content")
    return texts[0].strip()


def run():
    now = datetime.datetime.now(datetime.UTC)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    log.info("=" * 60)
    log.info("MOLTRUST HERALD AGENT v2")
    log.info(f"DID: {AGENT_DID}")
    log.info(f"Time: {now_str}")
    log.info("=" * 60)

    # --- Pre-flight checks ---
    errors = []
    if not API_KEY:
        errors.append("ANTHROPIC_API_KEY not set")
    if not X_CONSUMER_KEY:
        errors.append("X_CONSUMER_KEY not set")

    if errors:
        msg = "Herald pre-flight FAILED: " + ", ".join(errors)
        log.error(msg)
        write_heartbeat("error", msg)
        send_telegram(f"🚨 <b>Herald Error</b>\n{msg}")
        sys.exit(1)

    # --- Rate limit guard: skip if last post < 5h ago ---
    state = load_state()
    last_post = state.get("last_post_time", "")
    if last_post:
        try:
            last_dt = datetime.datetime.fromisoformat(last_post)
            hours_since = (now - last_dt).total_seconds() / 3600
            if hours_since < 5:
                log.info(f"Rate limit guard: last post {hours_since:.1f}h ago, skipping (min 5h)")
                write_heartbeat("skipped", f"Rate limit guard: {hours_since:.1f}h since last post")
                return
        except Exception:
            pass

    # --- Get briefing ---
    briefing = get_latest_briefing()
    if not briefing:
        msg = "No Scout briefing found"
        log.error(msg)
        write_heartbeat("error", msg)
        send_telegram(f"⚠️ <b>Herald Warning</b>\n{msg}")
        return

    # --- Generate tweet ---
    log.info("Generating post...")
    tweet = generate_post(briefing)

    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    log.info(f"Tweet: {tweet}")
    log.info(f"Length: {len(tweet)}/280")

    # --- Post to X ---
    x = get_x_client()
    tweet_id = None
    if x:
        result = x.create_tweet(text=tweet)
        tweet_id = str(result.data["id"])
        log.info(f"POSTED to X! Tweet ID: {tweet_id}")
    else:
        msg = "X credentials not available"
        log.warning(msg)
        write_heartbeat("error", msg)
        send_telegram(f"⚠️ <b>Herald Warning</b>\n{msg}")
        return

    # --- Save state ---
    state["last_post_time"] = now.isoformat()
    state["last_tweet_id"] = tweet_id
    state["consecutive_failures"] = 0
    save_state(state)

    # --- Save report ---
    date_str = now.strftime("%Y%m%d_%H%M")
    report_path = os.path.join(LOG_DIR, f"herald_{date_str}.md")
    with open(report_path, "w") as f:
        f.write(f"# MolTrust Herald\n")
        f.write(f"**Date:** {now_str}\n")
        f.write(f"**Agent:** {AGENT_NAME} ({AGENT_DID})\n\n")
        f.write(f"**Tweet:**\n{tweet}\n\n")
        f.write(f"**Auto-posted:** Yes (ID: {tweet_id})\n")

    log.info(f"Saved: {report_path}")
    write_heartbeat("ok", f"Posted tweet {tweet_id}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"FATAL: {e}\n{tb}")
        write_heartbeat("crash", str(e))
        # Track consecutive failures
        state = load_state()
        failures = state.get("consecutive_failures", 0) + 1
        state["consecutive_failures"] = failures
        state["last_error"] = str(e)
        state["last_error_time"] = datetime.datetime.now(datetime.UTC).isoformat()
        save_state(state)
        # Alert on every failure
        send_telegram(
            f"🚨 <b>Herald CRASHED</b> (#{failures})\n"
            f"<code>{str(e)[:300]}</code>"
        )
        sys.exit(1)
