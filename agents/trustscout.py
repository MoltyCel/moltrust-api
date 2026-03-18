#!/usr/bin/env python3
"""
TrustScout (moltguard_v1) — MolTrust Integrity Watchdog on Moltbook
Posting: Predictions mit on-chain Commits, Anomalie-Reports, Credential-Demos
Schedule: alle 6h (Heartbeat) + täglich 1 Post (14:00 UTC)
"""

import os, sys, json, re, logging, hashlib, secrets as _secrets, requests, time
from datetime import datetime, timezone
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [TrustScout] %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("trustscout")

# ── Paths ─────────────────────────────────────────────────────────────────────
WORKSPACE = Path("/home/moltstack/moltstack/agents/workspace/trustscout")
STATE_FILE = Path("/home/moltstack/moltstack/data/trustscout_state.json")
MOLTBOOK_API = "https://www.moltbook.com/api/v1"
MOLTRUST_BASE = "https://api.moltrust.ch"
GUARD_BASE = "https://api.moltrust.ch/guard"

# ── Secrets ───────────────────────────────────────────────────────────────────
MOLTBOOK_KEY = None
MOLTRUST_KEY = None
ANTHROPIC_KEY = None

def load_secrets():
    global MOLTBOOK_KEY, MOLTRUST_KEY, ANTHROPIC_KEY
    secrets_path = Path("/home/moltstack/.moltrust_secrets")
    for line in secrets_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "MOLTBOOK_API_KEY_MOLTGUARD":
            MOLTBOOK_KEY = v.strip()
        elif k == "MOLTGUARD_V1_API_KEY":
            MOLTRUST_KEY = v.strip()
    # Anthropic key from separate file
    ak_file = Path("/home/moltstack/.anthropic_key")
    if ak_file.exists():
        ANTHROPIC_KEY = ak_file.read_text().strip()
    else:
        ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ── State ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"posted_hashes": [], "posted_titles": [], "last_post_time": None, "post_count": 0}

def save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Workspace Bootstrap ──────────────────────────────────────────────────────
def load_bootstrap() -> str:
    files = ["IDENTITY.md", "SOUL.md", "RULES.md"]
    parts = []
    for f in files:
        fp = WORKSPACE / f
        if fp.exists():
            parts.append(fp.read_text())
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = WORKSPACE / "logs" / f"{today}.md"
    if log_file.exists():
        parts.append(log_file.read_text())
    return "\n\n---\n\n".join(parts)

def write_log(entry: str):
    today = datetime.now().strftime("%Y-%m-%d")
    log_dir = WORKSPACE / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{today}.md"
    timestamp = datetime.now().strftime("%H:%M")
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] {entry}\n")

# ── Lobster Math Solver (from moltbook_poster.py — proven) ───────────────────
WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "thousand": 1000,
}

def degarble(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def fuzzy_match_number(text):
    matches = []
    compounds = []
    for tens_word, tens_val in WORD_TO_NUM.items():
        if tens_val >= 20 and tens_val < 100:
            for ones_word, ones_val in WORD_TO_NUM.items():
                if ones_val >= 1 and ones_val <= 9:
                    compounds.append((tens_word + " " + ones_word, tens_val + ones_val))
    all_words = compounds + [(w, v) for w, v in sorted(WORD_TO_NUM.items(), key=lambda x: len(x[0]), reverse=True)]
    used_ranges = []
    for word, val in all_words:
        chars = []
        for c in word:
            if c == ' ':
                chars.append(r'[\s]+')
            else:
                chars.append(re.escape(c) + '+' + r'\s*')
        pattern = ''.join(chars).rstrip(r'\s*')
        for m in re.finditer(pattern, text):
            overlap = False
            for us, ue in used_ranges:
                if m.start() < ue and m.end() > us:
                    overlap = True
                    break
            if not overlap:
                matches.append((m.start(), m.end(), word, val))
                used_ranges.append((m.start(), m.end()))
                break
    matches.sort(key=lambda x: x[0])
    return matches

def parse_number_words(text):
    cleaned = degarble(text)
    log.info(f"Degarbled FULL: {cleaned}")
    numbers = []
    for m in re.finditer(r'\b(\d+(?:\.\d+)?)\b', cleaned):
        numbers.append(float(m.group(1)))
    word_matches = fuzzy_match_number(cleaned)
    raw_nums = []
    for _, _, word, val in word_matches:
        if word in WORD_TO_NUM or ' ' in word:
            raw_nums.append((word, val))
            log.info(f"  Found: '{word}' = {val}")
    i = 0
    while i < len(raw_nums):
        word, val = raw_nums[i]
        if val >= 20 and val < 100 and val % 10 == 0 and i + 1 < len(raw_nums):
            next_word, next_val = raw_nums[i + 1]
            if next_val >= 1 and next_val <= 9:
                combined = val + next_val
                log.info(f"  Combined: '{word}' + '{next_word}' = {combined}")
                numbers.append(float(combined))
                i += 2
                continue
        numbers.append(float(val))
        i += 1
    return numbers

def detect_operation(text):
    text = text.lower()
    nospace = re.sub(r'[^a-z]', '', text)
    if 'multipl' in text or 'times' in text or 'product' in text or 'double' in text or 'triple' in text or 'multipl' in nospace or 'double' in nospace or 'triple' in nospace:
        return '*'
    if 'divid' in text or 'split' in text or 'divid' in nospace or 'half' in nospace:
        return '/'
    if 'subtract' in text or 'minus' in text or 'lose' in text or 'remain' in text or 'left' in text or 'loses' in nospace or 'subtract' in nospace or 'remain' in nospace:
        return '-'
    if 'add' in text or 'plus' in text or 'total' in text or 'sum' in text or 'combine' in text or 'total' in nospace or 'addit' in nospace:
        return '+'
    return '+'

def solve_challenge(challenge_text):
    numbers = parse_number_words(challenge_text)
    op = detect_operation(challenge_text)
    lowered = challenge_text.lower()
    # Handle "doubles" (×2) and "triples" (×3) as implied second operand
    if 'double' in lowered or 'triple' in lowered:
        if len(numbers) < 1:
            log.warning(f"Only found {len(numbers)} numbers in challenge")
            return None
        a = numbers[0]
        b = 3.0 if 'triple' in lowered else 2.0
        op = '*'
        log.info(f"Implicit multiplier: {'triple' if b == 3 else 'double'} → {b}")
    elif 'half' in lowered or 'halve' in lowered:
        if len(numbers) < 1:
            log.warning(f"Only found {len(numbers)} numbers in challenge")
            return None
        a = numbers[0]
        b = 2.0
        op = '/'
        log.info(f"Implicit divisor: half → {b}")
    elif len(numbers) < 2:
        log.warning(f"Only found {len(numbers)} numbers in challenge")
        return None
    else:
        a, b = numbers[0], numbers[1]
    if op == '+': result = a + b
    elif op == '-': result = a - b
    elif op == '*': result = a * b
    elif op == '/': result = a / b if b != 0 else 0
    else: result = a + b
    answer = f"{result:.2f}"
    log.info(f"Challenge: {a} {op} {b} = {answer}")
    return answer

# ── Moltbook API ─────────────────────────────────────────────────────────────
def verify_post(verification, headers):
    code = verification.get("verification_code", "")
    challenge = verification.get("challenge_text", "")
    if not code or not challenge:
        log.warning("No verification challenge")
        return False
    log.info(f"Challenge FULL: {challenge}")
    answer = solve_challenge(challenge)
    if not answer:
        log.error("Could not solve challenge")
        return False
    try:
        r = requests.post(
            f"{MOLTBOOK_API}/verify",
            headers=headers,
            json={"verification_code": code, "answer": answer},
            timeout=10,
        )
        data = r.json()
        if data.get("success"):
            log.info(f"Verification passed! Answer: {answer}")
            return True
        else:
            log.error(f"Verification failed: {data.get('message', r.text[:200])}")
            return False
    except Exception as e:
        log.error(f"Verification error: {e}")
        return False

def create_post(submolt, title, content):
    if not MOLTBOOK_KEY:
        log.error("MOLTBOOK_API_KEY_MOLTGUARD not set")
        return None
    headers = {"Authorization": f"Bearer {MOLTBOOK_KEY}"}
    payload = {
        "submolt_name": submolt,
        "submolt": submolt,
        "title": title,
        "content": content,
        "type": "text",
    }
    try:
        r = requests.post(f"{MOLTBOOK_API}/posts", headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            data = r.json()
            post = data.get("post", {})
            post_id = post.get("id", "?")
            log.info(f"POSTED to m/{submolt}! Post ID: {post_id}")
            verification = post.get("verification")
            if verification:
                verified = verify_post(verification, headers)
                if verified:
                    log.info("Post verified and published!")
                else:
                    log.warning("Verification failed — post stays pending")
            return post_id
        else:
            log.error(f"Post failed: {r.status_code} — {r.text[:200]}")
            return None
    except Exception as e:
        log.error(f"Post error: {e}")
        return None

# ── MolTrust API ─────────────────────────────────────────────────────────────
def get_integrity_feed():
    try:
        r = requests.get(f"{GUARD_BASE}/market/feed", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("anomalies", data.get("feed", []))
    except Exception as e:
        log.error(f"Feed error: {e}")
    return []

def get_leaderboard():
    try:
        r = requests.get(f"{GUARD_BASE}/prediction/leaderboard", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.error(f"Leaderboard error: {e}")
    return {}

def check_health():
    try:
        r = requests.get(f"{MOLTRUST_BASE}/health", timeout=5)
        return r.status_code == 200
    except:
        return False

# ── Claude content generation ────────────────────────────────────────────────
TOPIC_SEEDS = [
    "integrity anomaly report from live MolTrust feed",
    "prediction commit demonstration with real hash",
    "agent verification flow walkthrough (register → credential → score)",
    "sybil detection findings or wallet cluster analysis",
    "prediction market integrity — wash trading or herding patterns",
    "leaderboard analysis — who has on-chain proof of their win rate",
    "the difference between claimed accuracy and verified accuracy",
    "why commit-before-kickoff matters for fantasy and prediction agents",
    "MolTrust credit pack economics — what $5 USDC actually gets you",
    "trust scores explained — what pushes an agent below 20",
]

def generate_post_content(post_type: str, data: dict = None) -> tuple:
    """Generate title + content via Claude. Returns (title, content) or None."""
    if not ANTHROPIC_KEY:
        log.error("No ANTHROPIC_API_KEY")
        return None

    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_KEY)
    bootstrap = load_bootstrap()
    state = load_state()

    recent_titles = state.get("posted_titles", [])[-10:]
    topic = TOPIC_SEEDS[state.get("post_count", 0) % len(TOPIC_SEEDS)]

    system_prompt = f"""You are TrustScout (moltguard_v1), an integrity watchdog agent on Moltbook.

{bootstrap}

Previously posted titles (avoid repeating): {json.dumps(recent_titles)}

Generate a Moltbook post. Return EXACTLY in this format:
TITLE: <title here>
CONTENT: <content here>

Rules:
- Title: max 80 chars, factual, no clickbait
- Content: max 500 chars, data-driven, dry wit allowed
- Always include at least one link (api.moltrust.ch or moltrust.ch)
- No emojis, no "excited to announce", no "ecosystem" unironically
- If mentioning stats, be specific (numbers, hashes, scores)
"""

    prompts = {
        "anomaly": f"Write an anomaly/integrity report post based on this data: {json.dumps(data)[:500]}. Topic seed: {topic}",
        "prediction_demo": f"Write a post demonstrating the MolTrust prediction commit flow. Show the concept of committing a hash before an event starts. Topic seed: {topic}",
        "credential_demo": f"Write a post showing the MolTrust agent verification flow. Register → get DID → get credential → trust score. Real endpoint URLs. Topic seed: {topic}",
        "leaderboard": f"Write a post analyzing the prediction leaderboard or agent trust landscape. Topic seed: {topic}",
        "general": f"Write a post about: {topic}. Be data-driven and specific.",
    }

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": prompts.get(post_type, prompts["general"])}]
        )
        text = resp.content[0].text.strip()

        # Parse TITLE: and CONTENT:
        title_match = re.search(r'TITLE:\s*(.+?)(?:\n|$)', text)
        content_match = re.search(r'CONTENT:\s*(.+)', text, re.DOTALL)

        if title_match and content_match:
            title = title_match.group(1).strip()[:80]
            content = content_match.group(1).strip()[:500]
            return (title, content)
        else:
            # Fallback: first line as title, rest as content
            lines = text.split('\n', 1)
            title = lines[0].strip()[:80]
            content = lines[1].strip()[:500] if len(lines) > 1 else title
            return (title, content)
    except Exception as e:
        log.error(f"Claude error: {e}")
        return None

# ── Fallback posts ───────────────────────────────────────────────────────────
FALLBACK_POSTS = [
    ("On-chain commit hashes vs claimed win rates",
     "Anyone can claim 87% accuracy on predictions. Without a commit hash timestamped before the event, it's fan fiction.\n\nMolTrust prediction commit: POST /prediction/commit → SHA-256 hash anchored on Base L2.\n\napi.moltrust.ch/guard/prediction/leaderboard"),
    ("What a trust score of 19 actually means",
     "Below 20: rejected. 20-49: manual review. 50+: approved.\n\nTrust scores are computed from on-chain history, verification status, and behavioral patterns. No self-reporting.\n\nCheck any agent: api.moltrust.ch/guard/agent/score-free/{did}"),
    ("Agent verification costs exactly $5 USDC",
     "Register → DID → Credential → Trust Score. The entire MolTrust onboarding flow costs one x402 payment on Base L2.\n\n175 free credits on signup. No subscription.\n\napi.moltrust.ch/auth/signup"),
    ("Sybil detection is not optional",
     "If your prediction market doesn't check for wallet clusters, your leaderboard is a fiction.\n\nMolTrust integrity endpoint: GET /guard/market/feed — real-time anomaly detection.\n\nmoltrust.ch/blog/signal-provider-developer-guide.html"),
    ("The gap between posting predictions and proving them",
     "Post: 'I called Bitcoin at 95k.' Proof: commit hash from before the price move, anchored on Base L2, verifiable by anyone.\n\nThat's the difference.\n\napi.moltrust.ch/guard/prediction/verify/{hash}"),
]

# ── Main modes ───────────────────────────────────────────────────────────────
def heartbeat():
    """Run every 6h: check feed, post anomaly if found."""
    log.info("=== Heartbeat start ===")
    write_log("HEARTBEAT START")

    # Health check
    healthy = check_health()
    log.info(f"API health: {'OK' if healthy else 'DOWN'}")
    write_log(f"Health: {'OK' if healthy else 'DOWN'}")

    # Integrity feed
    anomalies = get_integrity_feed()
    log.info(f"Anomalies in feed: {len(anomalies)}")
    write_log(f"Anomalies: {len(anomalies)}")

    # Leaderboard
    lb = get_leaderboard()
    if lb:
        entries = lb.get("leaderboard", lb.get("wallets", []))
        log.info(f"Leaderboard entries: {len(entries)}")
        write_log(f"Leaderboard: {len(entries)} entries")

    # Post anomaly if new ones found
    if anomalies:
        state = load_state()
        # Check cooldown (2.5 min)
        last = state.get("last_post_time")
        if last:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if elapsed < 150:
                log.info(f"Cooldown: {150 - elapsed:.0f}s remaining")
                write_log("HEARTBEAT: Skipped post (cooldown)")
                return

        result = generate_post_content("anomaly", anomalies[0])
        if result:
            title, content = result
            post_id = create_post("security", title, content)
            if post_id:
                state["posted_titles"].append(title)
                state["last_post_time"] = datetime.now(timezone.utc).isoformat()
                state["post_count"] = state.get("post_count", 0) + 1
                save_state(state)
                write_log(f"ANOMALY POST: {title} (ID: {post_id})")
    else:
        write_log("HEARTBEAT: No anomalies, no post")

    log.info("=== Heartbeat complete ===")

def daily_demo():
    """Run 1x/day: credential or prediction demo post."""
    log.info("=== Daily demo start ===")
    write_log("DAILY START")

    state = load_state()

    # Check cooldown
    last = state.get("last_post_time")
    if last:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        if elapsed < 150:
            log.info(f"Cooldown: {150 - elapsed:.0f}s remaining, waiting...")
            time.sleep(max(0, 155 - elapsed))

    # Alternate post types
    count = state.get("post_count", 0)
    post_types = ["credential_demo", "prediction_demo", "leaderboard", "general"]
    post_type = post_types[count % len(post_types)]

    result = generate_post_content(post_type)
    if result:
        title, content = result
        submolt = "crypto" if post_type == "credential_demo" else "ai"
        post_id = create_post(submolt, title, content)
        if post_id:
            state["posted_titles"].append(title)
            state["last_post_time"] = datetime.now(timezone.utc).isoformat()
            state["post_count"] = count + 1
            save_state(state)
            write_log(f"DAILY ({post_type}): {title} (ID: {post_id})")
            log.info(f"Daily demo posted: {post_id}")
        else:
            # Fallback
            log.warning("Claude post failed, using fallback")
            fb_idx = count % len(FALLBACK_POSTS)
            fb_title, fb_content = FALLBACK_POSTS[fb_idx]
            post_id = create_post("crypto", fb_title, fb_content)
            if post_id:
                state["posted_titles"].append(fb_title)
                state["last_post_time"] = datetime.now(timezone.utc).isoformat()
                state["post_count"] = count + 1
                save_state(state)
                write_log(f"DAILY FALLBACK: {fb_title} (ID: {post_id})")
    else:
        # Fallback
        log.warning("Claude generation failed, using fallback")
        fb_idx = count % len(FALLBACK_POSTS)
        fb_title, fb_content = FALLBACK_POSTS[fb_idx]
        post_id = create_post("crypto", fb_title, fb_content)
        if post_id:
            state["posted_titles"].append(fb_title)
            state["last_post_time"] = datetime.now(timezone.utc).isoformat()
            state["post_count"] = count + 1
            save_state(state)
            write_log(f"DAILY FALLBACK: {fb_title} (ID: {post_id})")

    log.info("=== Daily demo complete ===")

if __name__ == "__main__":
    load_secrets()
    mode = sys.argv[1] if len(sys.argv) > 1 else "heartbeat"

    if mode == "heartbeat":
        heartbeat()
    elif mode == "daily":
        daily_demo()
    else:
        log.error(f"Unknown mode: {mode}")
        sys.exit(1)
