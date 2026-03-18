"""MolTrust Herald Agent v3 — Claude-Generated Tweets
=================================================
Fetches MoltGuard anomaly feed for context, generates tweets via Claude API.
Fallback: curated pool if Claude unavailable.
Rate limit: min 5h between posts (X Free Tier safe).

Cron: 4x/day (07, 12, 17, 22 UTC)
"""

import os, sys, datetime, json, logging, traceback, random, re
import httpx
from requests_oauthlib import OAuth1
import requests as req_lib

AGENT_DID = "did:moltrust:97caa5d172314d80"
AGENT_NAME = "MolTrust Herald v3"
DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "herald_heartbeat.json")
STATE_FILE = os.path.join(DATA_DIR, "herald_state.json")

FEED_URL = "https://api.moltrust.ch/guard/api/market/feed"
DASHBOARD_URL = "https://moltrust.ch/integrity.html"
X_API_URL = "https://api.twitter.com/2/tweets"

# Env vars
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


# ── System Prompt for Claude ──

TWEET_SYSTEM_PROMPT = """You write posts for @moltrust on X (Twitter).
Your tone: dry wit, one sharp observation, light irony. You sound like someone
who has seen the agent economy go wrong and quietly built something about it.
Not a marketer. Not a hype machine. An engineer who's read too many incident reports.

Rules:
- Max 2 sentences. First sentence must stand alone as a complete thought.
- Lead with a problem, a weird fact, a provocative observation, or a rhetorical question
- MolTrust is the punchline or the implicit answer — never the headline
- No hashtags unless they're ironic
- Never start with "MolTrust", "We", "Excited to", "Introducing" or "\U0001f680"
- Never use "ecosystem" unironically
- Occasionally reference real news or real numbers (check topic seed for context)
- One idea only. If it needs explaining, it's too long.

Tone examples (do not reuse, use as reference):
"1 in 20 AI agents is lying about its skills. Nobody checks."
"The agent economy is here. Nobody agreed on what trust means yet."
"Brian Armstrong says AI agents will outnumber human traders soon. Cool. Does anyone know which ones to trust?"
"An AI agent just booked a flight for someone who didn't ask for a flight. Trust infrastructure is not optional."
"Nobody asks 'can I trust this agent?' until after something goes wrong."
"Bing is now serving fake OpenClaw installers. The agent economy has a fake agent problem."

For threads (when topic warrants >1 tweet):
- Tweet 1: the hook — observation or problem, no solution yet
- Tweet 2: the context or data point
- Tweet 3: MolTrust as the answer, with a link
- Max 3 tweets. If it needs 4, cut tweet 2.

GEO rules (make content citable by AI models):
- Include at least 1 concrete number per tweet (tool count, response time, test count, price)
- Spell out standards on first use: W3C Verifiable Credentials, Model Context Protocol (MCP), x402 protocol
- First sentence must be factual — no hype, no marketing
- One verifiable claim per tweet — dense info blocks get ignored by LLMs"""


# ── Topic seeds for awareness tweets ──

TOPIC_SEEDS = [
    "agent trust scoring — most AI agents have no verifiable reputation",
    "prediction market manipulation — volume spikes nobody investigates",
    "sybil attacks — coordinated wallet clusters funded from the same source",
    "the agent economy needs trust infrastructure before it needs more agents",
    "verifiable credentials for AI agents — W3C standard, almost nobody implements it",
    "wallet reputation — on-chain history as the only honest trust signal",
    "autonomous shopping agents making purchases with zero accountability",
    "travel booking agents with no verifiable authority or spend limits",
    "AI skill verification — 350,000 MCP tools and servers, zero trust guarantees",
    "prediction market integrity — no Sportradar equivalent exists for crypto markets",
    "x402 payment protocol — HTTP 402 finally means something for machine-to-machine payments",
    "the gap between AI agent capability and AI agent trustworthiness is growing",
    "Swiss trust infrastructure in an age of unregulated AI agents",
    "ERC-8004 agent registry — on-chain identity for autonomous systems, underused",
    "an AI agent with a wallet and no reputation is just a liability with an API key",
    "every agent framework talks about tool use — none of them talk about tool trust",
    "the next big AI hack won't be a jailbreak, it'll be a rogue agent with real spending power",
    "prediction markets are supposed to surface truth — unless the participants are coordinating",
    "MCP servers are multiplying faster than anyone can audit them",
    "the phrase 'trustless' was always a lie — someone always trusts something",
    "brand product provenance — when shopping agents can't tell real Nikes from fakes, trust becomes a supply chain problem",
]


# ── Fallback tweets (when Claude API is unavailable) ──

FALLBACK_TWEETS = [
    "1 in 20 AI agents has no verifiable identity. The other 19 just haven't been checked yet. https://moltrust.ch",
    "Prediction markets process billions in volume with zero independent integrity monitoring. We built one. https://moltrust.ch/integrity.html",
    "Every agent framework ships tool use. None of them ship tool trust. https://api.moltrust.ch/guard",
    "The agent economy has a trust problem. Not a capability problem. https://moltrust.ch",
    "If your AI agent has a wallet but no reputation, it's not autonomous — it's unsupervised. https://moltrust.ch",
]


# ── Helpers ──

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
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def write_heartbeat(status: str, detail: str = ""):
    hb = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
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
            json.dump(state, f, indent=2)
    except Exception:
        pass


def fmt_vol(v):
    if not v:
        return "$0"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v / 1e3:.0f}K"


def get_x_auth():
    if not all([X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
        return None
    return OAuth1(X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET)


def post_tweet(text: str, reply_to: str | None = None) -> str | None:
    """Post a tweet via X API v2. Returns tweet ID or None."""
    auth = get_x_auth()
    if not auth:
        log.error("X credentials not available")
        return None

    if len(text) > 280:
        text = text[:277] + "..."

    payload = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}

    r = req_lib.post(X_API_URL, json=payload, auth=auth, timeout=15)
    if r.status_code in (200, 201):
        data = r.json()
        tid = data["data"]["id"]
        log.info(f"POSTED to X! Tweet ID: {tid}")
        return tid
    else:
        log.error(f"X API {r.status_code}: {r.text[:300]}")
        return None


# ── Claude API ──

def load_anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            with open(os.path.expanduser("~/.anthropic_key")) as f:
                key = f.read().strip()
        except Exception:
            pass
    return key


def generate_with_claude(context: str) -> str | None:
    """Generate tweet text via Claude API. Returns raw text or None."""
    key = load_anthropic_key()
    if not key:
        log.warning("No Anthropic API key available")
        return None

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "system": TWEET_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": context}],
            },
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.json()["content"][0]["text"].strip()
            # Remove wrapping quotes if present
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            return text
        else:
            log.warning(f"Claude API {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.warning(f"Claude API failed: {e}")
    return None


def parse_thread(text: str) -> list[str]:
    """Parse Claude output into thread parts. Returns list of tweet texts."""
    # Check for thread markers like "1/3", "2/3" or "1.", "2."
    parts = re.split(r'\n\s*(?:\d+[/\.]\d*\s*)', text)
    parts = [p.strip() for p in parts if p.strip()]

    # If no markers found, check for double newlines as separator
    if len(parts) <= 1:
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]

    # If still just one block, return as single tweet
    if len(parts) <= 1:
        return [text.strip()]

    # Only treat as thread if each part fits in 280 chars
    if all(len(p) <= 280 for p in parts[:3]):
        return parts[:3]

    # Otherwise return as single tweet
    return [text.strip()]


# ── Feed fetching ──

def fetch_feed() -> list:
    """Fetch MoltGuard anomaly feed."""
    try:
        resp = httpx.get(FEED_URL, timeout=15)
        if resp.status_code != 200:
            log.warning(f"Feed HTTP {resp.status_code}")
            return []
        data = resp.json()
        return data.get("markets", [])
    except Exception as e:
        log.error(f"Feed fetch failed: {e}")
        return []


# ── Tweet generation ──

def generate_anomaly_tweet(markets: list, state: dict) -> str | None:
    """Generate a tweet from anomaly data via Claude."""
    flagged = [m for m in markets if m.get("anomalyScore", 0) >= 30]
    if not flagged:
        return None

    # Avoid repeating last-tweeted market
    last_market_id = state.get("last_market_id", "")
    candidates = [m for m in flagged if m.get("marketId") != last_market_id]
    if not candidates:
        candidates = flagged

    # Pick highest score
    candidates.sort(key=lambda m: m.get("anomalyScore", 0), reverse=True)
    top = candidates[0]

    sigs = top.get("signals", {})
    active = []
    if sigs.get("volumeSpike"):
        active.append("volume spike")
    if sigs.get("priceVolumeDiv"):
        active.append("price-volume divergence")
    if sigs.get("walletConcentration"):
        active.append("wallet concentration")
    if sigs.get("newWalletInflux"):
        active.append("new wallet influx")

    market_id = top.get("marketId", "")
    api_cta = f"api.moltrust.ch/integrity/{market_id}" if market_id else ""

    context = (
        f"Write a single tweet (max 280 chars) based on this real anomaly data:\n\n"
        f"Market: \"{top.get('marketQuestion', '')}\"\n"
        f"Anomaly Score: {top.get('anomalyScore', 0)}/100\n"
        f"Signals: {', '.join(active) if active else 'multiple signals active'}\n"
        f"24h Volume Change: {fmt_vol(sigs.get('volumeChange24h', 0))}\n"
        f"Assessment: {top.get('assessment', 'Unusual trading patterns detected')}\n\n"
        f"End the tweet with:\nCheck it: {DASHBOARD_URL}\nAPI: {api_cta}\n\n"
        f"Do NOT just describe the data. Find the sharp angle."
    )

    tweet = generate_with_claude(context)
    if tweet and len(tweet) > 280 and api_cta in tweet:
        tweet = tweet.replace(f"\nAPI: {api_cta}", "").replace(f" | API: {api_cta}", "")
    if tweet:
        state["last_market_id"] = market_id
    return tweet


def generate_awareness_tweet(state: dict) -> str | None:
    """Generate an awareness tweet from topic seeds via Claude."""
    # Pick a seed, avoid recent ones
    recent_seeds = state.get("recent_seeds", [])
    available = [s for s in TOPIC_SEEDS if s not in recent_seeds]
    if not available:
        available = TOPIC_SEEDS
        recent_seeds = []

    seed = random.choice(available)
    recent_seeds.append(seed)
    state["recent_seeds"] = recent_seeds[-10:]

    # Get recent tweet texts to avoid repetition
    recent_tweets = state.get("recent_tweets", [])

    context = (
        f"Write a single tweet (max 280 chars) about this topic:\n\n"
        f"Topic: {seed}\n\n"
        f"Context: MolTrust provides trust infrastructure for the agent economy — "
        f"wallet scoring, sybil detection, prediction market integrity monitoring, "
        f"verifiable credentials (W3C), skill verification for AI agents. "
        f"Built in Switzerland. Live API at api.moltrust.ch/guard\n\n"
        f"Dashboard: {DASHBOARD_URL}\n"
        f"Main site: https://moltrust.ch\n\n"
    )
    if recent_tweets:
        context += f"Avoid similarity to these recent tweets:\n" + "\n".join(f"- {t}" for t in recent_tweets[-5:])

    return generate_with_claude(context)


def generate_fallback_tweet(state: dict) -> str:
    """Pick a curated fallback tweet when Claude is unavailable."""
    idx = state.get("fallback_idx", 0) % len(FALLBACK_TWEETS)
    tweet = FALLBACK_TWEETS[idx]
    state["fallback_idx"] = idx + 1
    return tweet


# ── Main ──

def run(dry_run: bool = False):
    now = datetime.datetime.now(datetime.timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")

    log.info("=" * 60)
    log.info("MOLTRUST HERALD AGENT v3 — Claude Mode")
    log.info(f"DID: {AGENT_DID}")
    log.info(f"Time: {now_str}")
    if dry_run:
        log.info("*** DRY RUN — will NOT post ***")
    log.info("=" * 60)

    # Pre-flight
    if not dry_run and not X_CONSUMER_KEY:
        msg = "X credentials not set"
        log.error(msg)
        write_heartbeat("error", msg)
        send_telegram(f"\U0001f6a8 <b>Herald Error</b>\n{msg}")
        sys.exit(1)

    # Rate limit guard: min 5h between posts
    state = load_state()
    if not dry_run:
        last_post = state.get("last_post_time", "")
        if last_post:
            try:
                last_dt = datetime.datetime.fromisoformat(last_post)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
                hours_since = (now - last_dt).total_seconds() / 3600
                if hours_since < 3:
                    log.info(f"Rate limit guard: {hours_since:.1f}h since last post (min 3h). Skipping.")
                    write_heartbeat("skipped", f"{hours_since:.1f}h since last post")
                    return
            except Exception as e:
                log.warning(f"Could not parse last_post_time: {e}")

    # Fetch anomaly feed
    log.info("Fetching MoltGuard anomaly feed...")
    markets = fetch_feed()
    log.info(f"Feed: {len(markets)} markets")

    # Generate tweet
    tweet = None
    mode = "anomaly"

    if markets:
        tweet = generate_anomaly_tweet(markets, state)

    if not tweet:
        mode = "awareness"
        tweet = generate_awareness_tweet(state)

    if not tweet:
        mode = "fallback"
        tweet = generate_fallback_tweet(state)

    # Parse for thread
    parts = parse_thread(tweet)
    is_thread = len(parts) > 1

    log.info(f"Mode: {mode}")
    if is_thread:
        log.info(f"Thread ({len(parts)} tweets):")
        for i, p in enumerate(parts):
            log.info(f"  [{i + 1}/{len(parts)}] ({len(p)}/280): {p}")
    else:
        log.info(f"Tweet ({len(parts[0])}/280):\n{parts[0]}")

    if dry_run:
        log.info("DRY RUN complete — not posting.")
        print(f"\n{'=' * 50}")
        print(f"MODE: {mode}")
        if is_thread:
            print(f"THREAD ({len(parts)} tweets):")
            for i, p in enumerate(parts):
                print(f"\n  [{i + 1}/{len(parts)}] ({len(p)} chars):")
                print(f"  {p}")
        else:
            print(f"TWEET ({len(parts[0])} chars):")
            print(f"\n  {parts[0]}")
        print(f"\n{'=' * 50}")
        return

    # Post
    tweet_ids = []
    reply_to = None
    for i, part in enumerate(parts):
        if len(part) > 280:
            part = part[:277] + "..."
        tid = post_tweet(part, reply_to=reply_to)
        if tid:
            tweet_ids.append(tid)
            reply_to = tid
        else:
            log.error(f"Failed to post part {i + 1}/{len(parts)}")
            break

    if tweet_ids:
        state["last_post_time"] = now.isoformat()
        state["last_tweet_id"] = tweet_ids[0]
        state["last_mode"] = mode
        state["consecutive_failures"] = 0
        # Track recent tweets for variety
        recent = state.get("recent_tweets", [])
        recent.append(parts[0][:100])
        state["recent_tweets"] = recent[-10:]
        save_state(state)

        # Save report
        date_str = now.strftime("%Y%m%d_%H%M")
        report_path = os.path.join(LOG_DIR, f"herald_{date_str}.md")
        with open(report_path, "w") as f:
            f.write(f"# MolTrust Herald v3\n")
            f.write(f"**Date:** {now_str}\n")
            f.write(f"**Mode:** {mode}\n")
            f.write(f"**Markets in feed:** {len(markets)}\n")
            f.write(f"**Thread:** {'Yes' if is_thread else 'No'}\n\n")
            for i, (part, tid) in enumerate(zip(parts, tweet_ids)):
                f.write(f"**Tweet {i + 1}:**\n{part}\n\n")
                f.write(f"**Tweet ID:** {tid}\n")
                f.write(f"**URL:** https://x.com/MolTrust/status/{tid}\n\n")
        log.info(f"Report: {report_path}")
        write_heartbeat("ok", f"Posted ({mode}): {tweet_ids[0]}")
    else:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
        save_state(state)
        msg = f"Failed to post tweet (attempt #{state['consecutive_failures']})"
        log.error(msg)
        write_heartbeat("error", msg)
        send_telegram(f"\u26a0\ufe0f <b>Herald v3</b>\n{msg}\nMode: {mode}")


if __name__ == "__main__":
    try:
        dry = "--dry-run" in sys.argv
        run(dry_run=dry)
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"FATAL: {e}\n{tb}")
        write_heartbeat("crash", str(e))
        state = load_state()
        failures = state.get("consecutive_failures", 0) + 1
        state["consecutive_failures"] = failures
        state["last_error"] = str(e)
        state["last_error_time"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_state(state)
        send_telegram(
            f"\U0001f6a8 <b>Herald v3 CRASHED</b> (#{failures})\n"
            f"<code>{str(e)[:300]}</code>"
        )
        sys.exit(1)


# ── Dev.to Publishing ──

DEVTO_API_KEY = os.getenv("DEVTO_API_KEY", "")

BLOG_GEO_PROMPT = """
GEO OPTIMIZATION RULES (apply to every blog post and Dev.to article):

1. ONE CLAIM PER PARAGRAPH — each paragraph contains exactly one
   verifiable fact. No dense multi-claim blocks.

2. MINIMUM 3 CONCRETE NUMBERS — every post must contain at least 3
   specific, verifiable figures. Examples:
   "33 tools across 7 verticals", "8 security checks",
   "completes in under 3 seconds", "$5 USDC via x402"

3. DEFINITIONS PARAGRAPH — after the intro hook, always include:
   "[Feature] is [what it is]. [How it works, 1 sentence].
   [Who needs it, 1 sentence]. It is part of MolTrust v0.7.0,
   available at moltrust.ch."

4. SPELL OUT STANDARDS — always write in full on first use:
   "W3C Decentralized Identifiers (DIDs)",
   "W3C Verifiable Credentials (VCs)",
   "Model Context Protocol (MCP)",
   "x402 payment protocol on Base L2"

5. NO MARKETING FLUFF IN LEDE — first 2 sentences must be factual.
   Never: "We are excited to announce..."
   Always: "[Feature] does [X]. It works by [Y]."

6. FAQ SECTION (posts > 600 words) — add at the end:
   ## Frequently Asked Questions
   What is [Feature]? / Who is it for? /
   How much does it cost? / What standards does it use?
   Answers: 1-2 sentences, factual, citable.

7. COMPETITOR CONTEXT (optional, max 1x per post):
   "Unlike centralized reputation systems, MolTrust credentials
   are verifiable by any W3C-compliant resolver without
   contacting MolTrust servers."

PRE-PUBLISH CHECKLIST:
[ ] 3+ concrete numbers present?
[ ] Definitions paragraph present?
[ ] Standards spelled out in full?
[ ] No marketing fluff in first 2 sentences?
[ ] FAQ present (if post > 600 words)?
[ ] One claim per paragraph?
"""


def post_to_github_discussions(title: str, body: str,
                                category_id: str = None) -> dict:
    """
    Post a new discussion to MoltyCel/moltrust-mcp-server.
    Returns: {"url": "...", "number": N} or {"error": "..."}
    """
    token = os.getenv("GH_TOKEN", "")
    if not token:
        try:
            with open("/home/moltstack/.moltrust_secrets") as f:
                for line in f:
                    if line.startswith("GH_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    if not token:
        return {"error": "GH_TOKEN not available"}

    headers = {"Authorization": f"bearer {token}", "Content-Type": "application/json"}

    # Get repo node ID
    repo_resp = req_lib.get(
        "https://api.github.com/repos/MoltyCel/moltrust-mcp-server",
        headers={"Authorization": f"token {token}"},
        timeout=15,
    ).json()
    repo_node_id = repo_resp.get("node_id")
    if not repo_node_id:
        return {"error": f"Could not get repo node_id: {repo_resp.get('message', '?')}"}

    # Get category ID — prefer "Announcements", fallback to "Show and tell", then first
    if not category_id:
        cat_query = """
        query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            discussionCategories(first: 10) {
              nodes { id name }
            }
          }
        }
        """
        cat_resp = req_lib.post(
            "https://api.github.com/graphql",
            json={"query": cat_query, "variables": {"owner": "MoltyCel", "name": "moltrust-mcp-server"}},
            headers=headers,
            timeout=15,
        ).json()
        cats = cat_resp.get("data", {}).get("repository", {}).get("discussionCategories", {}).get("nodes", [])
        for preferred in ["announcements", "show and tell"]:
            for cat in cats:
                if cat["name"].lower() == preferred:
                    category_id = cat["id"]
                    break
            if category_id:
                break
        if not category_id and cats:
            category_id = cats[0]["id"]
        if not category_id:
            return {"error": "No discussion categories found"}

    # Create discussion via GraphQL mutation
    mutation = """
    mutation($repoId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
      createDiscussion(input: {
        repositoryId: $repoId,
        categoryId: $categoryId,
        title: $title,
        body: $body
      }) {
        discussion { url number }
      }
    }
    """
    resp = req_lib.post(
        "https://api.github.com/graphql",
        json={"query": mutation, "variables": {
            "repoId": repo_node_id,
            "categoryId": category_id,
            "title": title,
            "body": body,
        }},
        headers=headers,
        timeout=15,
    ).json()

    if "errors" in resp:
        return {"error": str(resp["errors"])}

    discussion = resp.get("data", {}).get("createDiscussion", {}).get("discussion", {})
    if not discussion:
        return {"error": f"Unexpected response: {str(resp)[:200]}"}

    log.info(f"GitHub Discussion created: {discussion['url']}")
    return {"url": discussion["url"], "number": discussion["number"]}



def send_hn_submitlink(title: str, url: str, note: str = "") -> bool:
    """
    Schickt einen HN-Submitlink via Telegram.
    Kein automatisches Posten — nur den Link bereitstellen.
    """
    import urllib.parse
    hn_url = (
        "https://news.ycombinator.com/submitlink?"
        f"u={urllib.parse.quote(url, safe='')}"
        f"&t={urllib.parse.quote(title, safe='')}"
    )
    msg = (
        f"\U0001f7e0 *HN Submit bereit*\n\n"
        f"*{title}*\n"
        f"URL: {url}\n\n"
        f"{('_' + note + '_\\n\\n') if note else ''}"
        f"\U0001f449 [Jetzt auf HN submitten]({hn_url})\n\n"
        f"_Bester Zeitpunkt: 09:00\u201311:00 US-ET (15:00\u201317:00 UTC)_"
    )
    return send_telegram(msg)


def post_to_devto(title: str, body_markdown: str, tags: list, canonical_url: str,
                  apply_geo: bool = True) -> dict:
    """Publish an article to Dev.to. If apply_geo=True, logs GEO checklist reminder."""
    if not DEVTO_API_KEY:
        return {"error": "DEVTO_API_KEY not set. Add it to ~/.moltrust_secrets"}
    if apply_geo:
        log.info("GEO checklist: Verify 3+ numbers, definitions paragraph, standards spelled out, factual lede, FAQ if >600w")
    response = req_lib.post(
        "https://dev.to/api/articles",
        headers={
            "api-key": DEVTO_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "article": {
                "title": title,
                "body_markdown": body_markdown,
                "published": True,
                "tags": tags,
                "canonical_url": canonical_url
            }
        },
        timeout=30,
    )
    if response.status_code in (200, 201):
        data = response.json()
        devto_url = data.get("url", "")
        log.info(f"Dev.to article published: {devto_url}")

        # Auto-post to GitHub Discussions
        if devto_url:
            gh_body = (
                f"## {title}\n\n"
                f"{body_markdown[:300].strip()}...\n\n"
                f"**Read the full guide:** {canonical_url}\n"
                f"**Dev.to:** {devto_url}\n\n"
                f"---\n"
                f"*Posted by Herald — MolTrust automated publishing*"
            )
            gh_result = post_to_github_discussions(
                title=f"[Developer Guide] {title}",
                body=gh_body,
            )
            if "url" in gh_result:
                log.info(f"GitHub Discussion: {gh_result['url']}")
                data["github_discussion"] = gh_result
            else:
                log.warning(f"GitHub Discussion failed: {gh_result}")

        # Optional: notify via Telegram with HN submitlink
        # if os.getenv('HN_AUTO_NOTIFY', 'false').lower() == 'true':
        #     send_hn_submitlink(title=title, url=canonical_url)

        return data
    else:
        log.error(f"Dev.to publish failed: {response.status_code} {response.text[:300]}")
        return {"error": f"HTTP {response.status_code}", "detail": response.text[:300]}
