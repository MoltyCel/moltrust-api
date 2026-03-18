#!/usr/bin/env python3
"""MoltGuard — Integrity Watchdog for Agent Prediction Markets.

Monitors Polymarket for manipulation patterns, posts analysis to Moltbook.
Built by MolTrust (moltrust.ch).

Usage:
    moltguard.py scan        — Scan Polymarket, detect anomalies, save results
    moltguard.py post-brief  — Post market integrity daily brief to Moltbook
    moltguard.py post-deep   — Post deep-dive on a specific anomaly
    moltguard.py post-edu    — Post educational content about trust infra
"""

import argparse
import json
import logging
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENT_NAME = "MoltGuard"
AGENT_DID = ""  # Set after registration, loaded from secrets

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
POLYMARKET_BASE = "https://gamma-api.polymarket.com"
MOLTRUST_API = "https://api.moltrust.ch"

DATA_DIR = Path.home() / "moltstack" / "data"
SCAN_FILE = DATA_DIR / "moltguard_scan.json"
STATE_FILE = DATA_DIR / "trustscout_state.json"
LOG_FILE = Path.home() / "moltstack" / "logs" / "moltguard.log"

# Anomaly thresholds
ZSCORE_THRESHOLD = 3.0
PRICE_MOVE_THRESHOLD = 0.15  # 15%
LOW_LIQUIDITY_THRESHOLD = 10_000  # $10k

# Submolts to rotate through
SUBMOLTS = ["general", "crypto", "technology", "business"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("moltguard")

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

MOLTBOOK_KEY = ""
ANTHROPIC_KEY = ""


def load_key(name: str) -> str:
    secrets = Path.home() / ".moltrust_secrets"
    if secrets.exists():
        for line in secrets.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if line.startswith("export "):
                line = line[7:]
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(name, "")


def init_keys():
    global MOLTBOOK_KEY, ANTHROPIC_KEY, AGENT_DID
    MOLTBOOK_KEY = load_key("MOLTGUARD_MOLTBOOK_KEY")
    AGENT_DID = load_key("MOLTGUARD_DID")

    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_KEY:
        key_file = Path.home() / ".anthropic_key"
        if key_file.exists():
            ANTHROPIC_KEY = key_file.read_text().strip()


# ---------------------------------------------------------------------------
# Math challenge solver (from ambassador.py)
# ---------------------------------------------------------------------------


def _collapse(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


_NUM_BASE = [
    ("zero", 0), ("one", 1), ("two", 2), ("three", 3), ("four", 4),
    ("five", 5), ("six", 6), ("seven", 7), ("eight", 8), ("nine", 9),
    ("ten", 10), ("eleven", 11), ("twelve", 12), ("thirteen", 13),
    ("fourteen", 14), ("fifteen", 15), ("sixteen", 16), ("seventeen", 17),
    ("eighteen", 18), ("nineteen", 19), ("twenty", 20), ("thirty", 30),
    ("forty", 40), ("fifty", 50), ("sixty", 60), ("seventy", 70),
    ("eighty", 80), ("ninety", 90),
]
NUM_LOOKUP: dict[str, int] = {}
for _w, _v in _NUM_BASE:
    NUM_LOOKUP[_w] = _v
    _c = _collapse(_w)
    if _c != _w:
        NUM_LOOKUP[_c] = _v

_OP_BASE = [
    ("plus", "+"), ("add", "+"), ("added", "+"), ("adding", "+"), ("adds", "+"),
    ("minus", "-"), ("subtract", "-"), ("subtracted", "-"),
    ("less", "-"), ("reduced", "-"), ("reduces", "-"),
    ("decreased", "-"), ("decreases", "-"), ("decrease", "-"),
    ("slows", "-"), ("slowed", "-"),
    ("times", "*"), ("multiplied", "*"), ("multiply", "*"),
    ("divided", "/"), ("divides", "/"), ("over", "/"),
]
OP_LOOKUP: dict[str, str] = {}
for _w, _o in _OP_BASE:
    OP_LOOKUP[_w] = _o
    _c = _collapse(_w)
    if _c != _w:
        OP_LOOKUP[_c] = _o


def _combine_tens_units(nums: list) -> list:
    combined = []
    i = 0
    while i < len(nums):
        v = nums[i]
        if 20 <= v <= 90 and i + 1 < len(nums) and 1 <= nums[i + 1] <= 9:
            combined.append(v + nums[i + 1])
            i += 2
        else:
            combined.append(v)
            i += 1
    return combined


def _compute(a: float, b: float, op: str) -> str | None:
    if op == "+":
        result = a + b
    elif op == "-":
        result = a - b
    elif op == "*":
        result = a * b
    elif op == "/":
        result = a / b if b != 0 else 0
    else:
        return None
    return f"{result:.2f}"


def solve_challenge(text: str) -> str | None:
    # Detect literal arithmetic operators before stripping them
    literal_op = None
    for sym, op_name in [("+", "+"), ("-", "-"), ("*", "*"), ("/", "/")]:
        pat = r"(?<=\s)" + re.escape(sym) + r"(?=\s)"
        if re.search(pat, text):
            literal_op = op_name
            break
    clean = re.sub(r"[^a-zA-Z ]+", "", text).lower()
    words = [_collapse(w) for w in clean.split() if w]
    nums: list[int] = []
    op: str | None = literal_op
    for w in words:
        if w in NUM_LOOKUP:
            nums.append(NUM_LOOKUP[w])
        elif w in OP_LOOKUP and op is None:
            op = OP_LOOKUP[w]
    if op is None:
        for i in range(len(words) - 1):
            compound = words[i] + words[i + 1]
            if compound in OP_LOOKUP:
                op = OP_LOOKUP[compound]
                break
    combined = _combine_tens_units(nums)
    if len(combined) >= 2 and op is not None:
        return _compute(combined[0], combined[1], op)

    stream = _collapse(re.sub(r"[^a-zA-Z]", "", text).lower())
    num_entries = sorted(NUM_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True)
    op_entries = sorted(OP_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True)
    used: set[int] = set()
    stream_nums: list[tuple[int, int]] = []
    for word, val in num_entries:
        for m in re.finditer(re.escape(word), stream):
            r = set(range(m.start(), m.end()))
            if not r & used:
                stream_nums.append((m.start(), val))
                used |= r
    stream_ops: list[tuple[int, str]] = []
    for word, op_val in op_entries:
        for m in re.finditer(re.escape(word), stream):
            r = set(range(m.start(), m.end()))
            if not r & used:
                stream_ops.append((m.start(), op_val))
                used |= r
                break
    stream_nums.sort()
    stream_ops.sort()
    s_nums = _combine_tens_units([v for _, v in stream_nums])
    s_op = stream_ops[0][1] if stream_ops else (op or "*")
    if len(s_nums) >= 2:
        return _compute(s_nums[0], s_nums[1], s_op)

    digits = [float(d) for d in re.findall(r"\d+\.?\d*", text)]
    if len(digits) >= 2:
        return _compute(digits[0], digits[1], op or "*")
    return None


# ---------------------------------------------------------------------------
# Moltbook API
# ---------------------------------------------------------------------------


def moltbook_post_req(client: httpx.Client, path: str, body: dict) -> dict | None:
    for attempt in range(3):
        try:
            r = client.post(
                f"{MOLTBOOK_BASE}{path}",
                headers={"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"},
                json=body,
                timeout=15,
            )
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code == 429:
                retry_after = r.json().get("retry_after_seconds", 30)
                log.info(f"Rate limited, waiting {retry_after}s (attempt {attempt + 1}/3)")
                time.sleep(retry_after + 1)
                continue
            log.warning(f"POST {path} -> {r.status_code}: {r.text[:300]}")
            return None
        except Exception as e:
            log.error(f"POST {path} error: {e}")
            return None
    log.warning(f"POST {path} failed after 3 attempts")
    return None


def solve_verification(client: httpx.Client, data: dict) -> bool:
    verification = data.get("verification") or data.get("post", {}).get("verification")
    if not verification:
        return True
    code = verification.get("verification_code", "")
    challenge = verification.get("challenge_text", "")
    if not code or not challenge:
        return True
    log.info(f"Verification challenge: {challenge[:80]}...")
    answer = solve_challenge(challenge)
    if not answer:
        log.error("Failed to solve math challenge")
        return False
    result = moltbook_post_req(client, "/verify", {"verification_code": code, "answer": answer})
    if result and result.get("success"):
        log.info("Verification solved!")
        return True
    log.error(f"Verification failed: {result}")
    return False


def _update_state(title: str):
    """Write state file so watchdog can track posting activity."""
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception:
        state = {}
    titles = state.get("posted_titles", [])
    titles.append(title)
    state["posted_titles"] = titles[-20:]  # keep last 20
    state["last_post_time"] = datetime.now(timezone.utc).isoformat()
    state["post_count"] = state.get("post_count", 0) + 1
    STATE_FILE.write_text(json.dumps(state, indent=2))


def post_to_moltbook(client: httpx.Client, title: str, content: str, submolt: str) -> bool:
    body = {
        "submolt_name": submolt,
        "title": title[:300],
        "content": content[:5000],
        "type": "text",
    }
    log.info(f"Posting to m/{submolt}: {title[:60]}...")
    result = moltbook_post_req(client, "/posts", body)
    if result:
        solved = solve_verification(client, result)
        post_id = result.get("post", {}).get("id") or result.get("id", "?")
        log.info(f"Posted! ID: {post_id}, verification: {'OK' if solved else 'FAILED'}")
        # Update state file for watchdog
        _update_state(title)
        return True
    log.error("Failed to post to Moltbook")
    return False


# ---------------------------------------------------------------------------
# Polymarket Scanner
# ---------------------------------------------------------------------------


def fetch_markets(client: httpx.Client) -> list[dict]:
    markets = []
    # Fetch top markets by 24h volume (most interesting for monitoring)
    for offset in range(0, 500, 100):
        try:
            r = client.get(
                f"{POLYMARKET_BASE}/markets",
                params={
                    "limit": 100,
                    "active": "true",
                    "offset": offset,
                    "order": "volume24hr",
                    "ascending": "false",
                },
                timeout=20,
            )
            if r.status_code != 200:
                log.warning(f"Polymarket API -> {r.status_code}")
                break
            batch = r.json()
            if not batch:
                break
            markets.extend(batch)
            # Stop if we're getting zero-volume markets
            last_vol = batch[-1].get("volume24hr", 0)
            if (isinstance(last_vol, (int, float)) and last_vol == 0) or len(batch) < 100:
                break
        except Exception as e:
            log.error(f"Polymarket fetch error: {e}")
            break
    log.info(f"Fetched {len(markets)} active markets from Polymarket")
    return markets


def analyze_markets(markets: list[dict], previous: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    prev_prices = previous.get("prices", {})

    # Collect volume data for Z-score calculation
    volumes_24h = []
    market_data = []
    for m in markets:
        vol_24h = m.get("volume24hr", 0)
        if isinstance(vol_24h, str):
            vol_24h = float(vol_24h) if vol_24h else 0
        total_vol = m.get("volume", "0")
        if isinstance(total_vol, str):
            total_vol = float(total_vol) if total_vol else 0
        liquidity = m.get("liquidityNum", 0)
        if isinstance(liquidity, str):
            liquidity = float(liquidity) if liquidity else 0

        prices = m.get("outcomePrices", [])
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except Exception:
                prices = []

        market_data.append({
            "id": m.get("id", ""),
            "question": m.get("question", "")[:200],
            "slug": m.get("slug", ""),
            "category": m.get("category", ""),
            "volume_24h": vol_24h,
            "volume_total": total_vol,
            "liquidity": liquidity,
            "prices": [float(p) if p else 0 for p in prices],
            "outcomes": m.get("outcomes", []),
            "end_date": m.get("endDate", ""),
        })
        if vol_24h > 0:
            volumes_24h.append(vol_24h)

    # Calculate Z-scores
    anomalies = []
    if len(volumes_24h) >= 10:
        mean_vol = statistics.mean(volumes_24h)
        stdev_vol = statistics.stdev(volumes_24h) if len(volumes_24h) > 1 else 1
        if stdev_vol == 0:
            stdev_vol = 1

        for md in market_data:
            flags = []

            # Volume spike detection
            if md["volume_24h"] > 0:
                zscore = (md["volume_24h"] - mean_vol) / stdev_vol
                if zscore > ZSCORE_THRESHOLD:
                    flags.append({
                        "type": "volume_spike",
                        "zscore": round(zscore, 2),
                        "volume_24h": md["volume_24h"],
                        "mean": round(mean_vol, 2),
                    })

            # Price movement detection
            prev_p = prev_prices.get(md["id"])
            if prev_p and md["prices"]:
                for i, price in enumerate(md["prices"]):
                    if i < len(prev_p) and prev_p[i] > 0:
                        move = abs(price - prev_p[i]) / prev_p[i]
                        if move > PRICE_MOVE_THRESHOLD:
                            flags.append({
                                "type": "price_move",
                                "outcome_index": i,
                                "previous": round(prev_p[i], 4),
                                "current": round(price, 4),
                                "change_pct": round(move * 100, 1),
                            })

            # Low liquidity with unusual volume
            if md["volume_total"] < LOW_LIQUIDITY_THRESHOLD and md["volume_24h"] > 0:
                vol_ratio = md["volume_24h"] / max(md["volume_total"], 1)
                if vol_ratio > 0.5:  # 24h volume is > 50% of total
                    flags.append({
                        "type": "low_liquidity_surge",
                        "volume_24h": md["volume_24h"],
                        "volume_total": md["volume_total"],
                        "ratio": round(vol_ratio, 2),
                    })

            if flags:
                anomalies.append({
                    "market": md,
                    "flags": flags,
                })
    else:
        log.warning(f"Not enough markets with volume for Z-score (got {len(volumes_24h)})")

    # Store current prices for next comparison
    current_prices = {}
    for md in market_data:
        if md["prices"]:
            current_prices[md["id"]] = md["prices"]

    scan_result = {
        "timestamp": now,
        "markets_scanned": len(markets),
        "markets_with_volume": len(volumes_24h),
        "mean_volume_24h": round(statistics.mean(volumes_24h), 2) if volumes_24h else 0,
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "prices": current_prices,
        "top_markets": sorted(
            [m for m in market_data if m["volume_24h"] > 0],
            key=lambda x: x["volume_24h"],
            reverse=True,
        )[:10],
    }

    return scan_result


def cmd_scan():
    """Scan Polymarket and save results."""
    log.info("=== SCAN: Polymarket market monitoring ===")

    # Load previous scan for price comparison
    previous = {}
    if SCAN_FILE.exists():
        try:
            previous = json.loads(SCAN_FILE.read_text())
        except Exception:
            pass

    with httpx.Client() as client:
        markets = fetch_markets(client)
        if not markets:
            log.error("No markets fetched")
            return

        result = analyze_markets(markets, previous)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCAN_FILE.write_text(json.dumps(result, indent=2))
    log.info(f"Scan complete: {result['markets_scanned']} markets, {result['anomaly_count']} anomalies")

    if result["anomalies"]:
        for a in result["anomalies"][:5]:
            q = a["market"]["question"][:60]
            flags = ", ".join(f["type"] for f in a["flags"])
            log.info(f"  ANOMALY: {q} [{flags}]")


# ---------------------------------------------------------------------------
# Claude content generation
# ---------------------------------------------------------------------------

MOLTGUARD_SYSTEM = """You are MoltGuard — the integrity watchdog for agent prediction markets.

You are built by MolTrust (moltrust.ch), the trust infrastructure for AI agents. You operate like Sportradar for traditional sports betting: you don't bet, you protect market integrity.

Your three core services:
1. Sybil Shield — detecting when multiple "independent" agents are controlled by one operator
2. Integrity Monitor — statistical anomaly detection on public market data
3. Compliance Layer — tamper-proof integrity reports anchored on Base blockchain

You are factual, data-driven, and precise. You cite sources and provide evidence.
You never speculate without data. You are direct about integrity concerns.

Formatting rules for Moltbook posts:
- Title: max 100 chars, compelling, no clickbait
- Content: max 2000 chars, structured with clear sections
- Always mention data source (Polymarket Gamma API)
- End with a question or call to discussion
- Mention MolTrust naturally when relevant (not every post)
- Use markdown formatting sparingly"""


def generate_content(prompt: str) -> tuple[str, str] | None:
    """Generate title + content via Claude. Returns (title, content) or None."""
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 800,
                "system": MOLTGUARD_SYSTEM,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.warning(f"Claude API -> {r.status_code}: {r.text[:200]}")
            return None

        data = r.json()
        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        if not texts:
            return None

        full_text = texts[0].strip()

        # Parse title and content
        all_lines = full_text.split("\n")
        title = ""
        content_start = 0
        for i, line in enumerate(all_lines):
            stripped = line.strip()
            if not stripped or stripped == "---":
                continue
            # Clean TITLE prefix (all markdown variants)
            cleaned = re.sub(r'^[\s#*]*(TITLE|Title|title)[\s*:]*', '', stripped).strip()
            cleaned = cleaned.lstrip('#').strip().strip('"*').strip()
            if cleaned:
                title = cleaned
                content_start = i + 1
                break
        content = "\n".join(all_lines[content_start:]).strip()
        for prefix in ["CONTENT:", "Content:", "**Content:**"]:
            if content.startswith(prefix):
                content = content[len(prefix):].strip()

        # Fallback: first 100 chars as title
        if not title:
            title = content[:100].split("\n")[0]

        return (title[:300], content[:5000])

    except Exception as e:
        log.error(f"Claude API error: {e}")
        return None


# ---------------------------------------------------------------------------
# Post commands
# ---------------------------------------------------------------------------


def _pick_submolt(mode: str) -> str:
    """Rotate submolts based on day of month and mode."""
    day = datetime.now(timezone.utc).day
    if mode == "post-brief":
        return SUBMOLTS[day % len(SUBMOLTS)]
    elif mode == "post-deep":
        return SUBMOLTS[(day + 1) % len(SUBMOLTS)]
    else:  # post-edu
        return SUBMOLTS[(day + 2) % len(SUBMOLTS)]


def load_scan_data() -> dict:
    if SCAN_FILE.exists():
        try:
            return json.loads(SCAN_FILE.read_text())
        except Exception:
            pass
    return {}


def cmd_post_brief():
    """Post market integrity daily brief."""
    log.info("=== POST-BRIEF: Market integrity daily brief ===")

    scan = load_scan_data()
    if not scan:
        log.error("No scan data available. Run 'scan' first.")
        return

    anomaly_count = scan.get("anomaly_count", 0)
    markets_scanned = scan.get("markets_scanned", 0)
    mean_vol = scan.get("mean_volume_24h", 0)
    top_markets = scan.get("top_markets", [])[:5]
    anomalies = scan.get("anomalies", [])[:3]

    top_summary = "\n".join(
        f"  - {m['question'][:80]} (24h vol: ${m['volume_24h']:,.0f})"
        for m in top_markets
    )
    anomaly_summary = ""
    if anomalies:
        anomaly_summary = "\nAnomalies detected:\n" + "\n".join(
            f"  - {a['market']['question'][:60]}: {', '.join(f['type'] for f in a['flags'])}"
            for a in anomalies
        )

    prompt = f"""Write a "Market Integrity Daily Brief" post for Moltbook.

Data from latest Polymarket scan:
- Markets scanned: {markets_scanned}
- Mean 24h volume: ${mean_vol:,.0f}
- Anomalies detected: {anomaly_count}
- Scan timestamp: {scan.get('timestamp', 'unknown')}

Top markets by volume:
{top_summary}
{anomaly_summary}

Format:
TITLE: [compelling title, max 100 chars]
[post content — structured, data-driven, max 1500 chars]

Include the data source (Polymarket Gamma API) and end with a discussion question.
If anomalies exist, highlight them. If not, note that markets look healthy."""

    result = generate_content(prompt)
    if not result:
        log.error("Failed to generate brief content")
        return

    title, content = result
    submolt = _pick_submolt("post-brief")

    with httpx.Client() as client:
        post_to_moltbook(client, title, content, submolt)


def cmd_post_deep():
    """Post deep-dive on a specific market or anomaly."""
    log.info("=== POST-DEEP: Market deep-dive ===")

    scan = load_scan_data()
    if not scan:
        log.error("No scan data available. Run 'scan' first.")
        return

    anomalies = scan.get("anomalies", [])
    top_markets = scan.get("top_markets", [])[:3]

    # Pick subject: anomaly if available, otherwise top market
    if anomalies:
        subject = anomalies[0]
        market = subject["market"]
        flags = subject["flags"]
        flag_details = json.dumps(flags, indent=2)
        prompt = f"""Write a deep-dive analysis post about this prediction market anomaly.

Market: {market['question']}
Category: {market['category']}
24h Volume: ${market['volume_24h']:,.0f}
Total Volume: ${market['volume_total']:,.0f}
Liquidity: ${market['liquidity']:,.0f}
Current Prices: {market['prices']}
Outcomes: {market['outcomes']}

Anomaly flags detected:
{flag_details}

Format:
TITLE: [compelling title, max 100 chars]
[deep analysis — explain what the anomaly means, possible causes (manipulation vs organic),
what traders should watch for, how verified agent identity could help. Max 2000 chars]

Be analytical and nuanced. Don't accuse anyone of manipulation — present the data and let
readers draw conclusions. Mention MolTrust's integrity monitoring role naturally."""
    elif top_markets:
        m = top_markets[0]
        prompt = f"""Write a deep-dive analysis post about this high-volume prediction market.

Market: {m['question']}
Category: {m['category']}
24h Volume: ${m['volume_24h']:,.0f}
Total Volume: ${m['volume_total']:,.0f}
Liquidity: ${m['liquidity']:,.0f}

Format:
TITLE: [compelling title, max 100 chars]
[analysis — volume drivers, market efficiency, integrity observations. Max 2000 chars]

Be data-driven. Mention data source (Polymarket Gamma API)."""
    else:
        log.warning("No markets to deep-dive on")
        return

    result = generate_content(prompt)
    if not result:
        log.error("Failed to generate deep-dive content")
        return

    title, content = result
    submolt = _pick_submolt("post-deep")

    with httpx.Client() as client:
        post_to_moltbook(client, title, content, submolt)


EDU_TOPICS = [
    {
        "topic": "Sybil Attacks in Prediction Markets",
        "prompt": """Write an educational post about Sybil attacks in agent prediction markets.

Explain:
- What a Sybil attack is (one operator running multiple "independent" agents)
- How it manifests in prediction markets (coordinated betting, fake consensus)
- Real-world parallels (wash trading in crypto, review farming)
- How W3C DIDs and Verifiable Credentials can help (unique identity binding)
- MolTrust's approach: DID registration + reputation scoring + on-chain anchoring

Format:
TITLE: [educational title, max 100 chars]
[accessible explanation, concrete examples, max 2000 chars]"""
    },
    {
        "topic": "Why Agent Identity Matters for Market Integrity",
        "prompt": """Write an educational post about why verifiable agent identity is critical for prediction market integrity.

Cover:
- The trust problem: how do you know an agent is who they claim to be?
- Anonymous vs pseudonymous vs verified: the spectrum
- W3C Decentralized Identifiers (DIDs): portable, self-sovereign identity
- Verifiable Credentials: tamper-proof claims (like a passport for agents)
- The MolTrust approach: did:moltrust:... + Ed25519 signed VCs + Base blockchain anchoring

Format:
TITLE: [educational title, max 100 chars]
[clear, accessible, max 2000 chars]"""
    },
    {
        "topic": "Front-Running and Information Asymmetry in Agent Markets",
        "prompt": """Write an educational post about front-running risks in AI agent prediction markets.

Explain:
- What front-running is (acting on information before others)
- How AI agents can exploit speed advantages in prediction markets
- The role of transparency and audit trails
- How on-chain reputation (ERC-8004) creates accountability
- Why integrity monitoring (like MoltGuard) is the Sportradar of agent markets

Format:
TITLE: [educational title, max 100 chars]
[analytical, data-aware, max 2000 chars]"""
    },
    {
        "topic": "The Agent Reputation Problem",
        "prompt": """Write an educational post about the reputation problem in the agent economy.

Cover:
- Reputation farming: agents gaming review systems
- Cold start problem: how does a new agent build trust?
- Cross-platform reputation portability
- MolTrust's solution: 1-5 star ratings, Verifiable Credentials, blockchain anchoring
- The vision: a trust layer that follows agents everywhere

Format:
TITLE: [educational title, max 100 chars]
[engaging, practical examples, max 2000 chars]"""
    },
    {
        "topic": "ERC-8004: The On-Chain Standard for Agent Identity",
        "prompt": """Write an educational post about ERC-8004 and what it means for agent prediction markets.

Cover:
- What ERC-8004 is: on-chain identity + reputation + validation for AI agents
- Three registries: IdentityRegistry (ERC-721), ReputationRegistry, ValidationRegistry
- Deployed on Base L2 (gas costs ~$0.001/tx)
- How MolTrust bridges off-chain trust (DIDs, VCs) with on-chain identity (ERC-8004)
- Why this matters for prediction market integrity

Format:
TITLE: [educational title, max 100 chars]
[informative, concrete, max 2000 chars]"""
    },
]


def cmd_post_edu():
    """Post educational content about trust infrastructure."""
    log.info("=== POST-EDU: Educational post ===")

    # Rotate through topics based on day of month
    day = datetime.now(timezone.utc).day
    topic = EDU_TOPICS[day % len(EDU_TOPICS)]

    log.info(f"Topic: {topic['topic']}")

    result = generate_content(topic["prompt"])
    if not result:
        log.error("Failed to generate educational content")
        return

    title, content = result
    submolt = _pick_submolt("post-edu")

    with httpx.Client() as client:
        post_to_moltbook(client, title, content, submolt)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="MoltGuard — Integrity Watchdog")
    parser.add_argument(
        "command",
        choices=["scan", "post-brief", "post-deep", "post-edu"],
        help="scan=monitor markets, post-*=post to Moltbook",
    )
    args = parser.parse_args()

    init_keys()

    now = datetime.now(timezone.utc)
    log.info(f"\n{'=' * 60}")
    log.info(f"MOLTGUARD — {args.command}")
    log.info(f"DID: {AGENT_DID or 'not registered yet'}")
    log.info(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"{'=' * 60}")

    if args.command == "scan":
        cmd_scan()
    elif args.command in ("post-brief", "post-deep", "post-edu"):
        if not MOLTBOOK_KEY:
            log.error("MOLTGUARD_MOLTBOOK_KEY not set")
            return
        if not ANTHROPIC_KEY:
            log.error("ANTHROPIC_API_KEY not set")
            return
        if args.command == "post-brief":
            cmd_post_brief()
        elif args.command == "post-deep":
            cmd_post_deep()
        elif args.command == "post-edu":
            cmd_post_edu()

    log.info("Done.\n")


if __name__ == "__main__":
    main()
