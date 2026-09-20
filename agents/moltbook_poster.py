"""MolTrust Moltbook Ambassador Agent
=====================================
Posts 2x/day to Moltbook (moltbook.com) in relevant submolts.
Uses Claude to generate posts with dry wit and mild provocation.

Cron: 2x/day (09:00, 19:00 UTC)
"""

import os, sys, json, logging, random, datetime, hashlib, re
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.moltbook_verify import solve_challenge  # shared LLM solver: garbled-operator safe

AGENT_NAME = "moltrust-agent"
from activity import mark_active  # FIX 1: un-ghost on post
POSTER_DID = "did:moltrust:ambassador0001"  # "moltrust-agent" Moltbook account = Ambassador
DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")
STATE_FILE = os.path.join(DATA_DIR, "moltbook_state.json")

MOLTBOOK_API = "https://www.moltbook.com/api/v1"
MOLTBOOK_KEY = os.getenv("MOLTBOOK_AGENT_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("moltbook")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


# ── Post Generation ──────────────────────────────────────────────────────────

SUBMOLTS = ["agents", "security", "crypto", "ai", "infrastructure", "general"]

POST_SYSTEM_PROMPT = """You write posts for MolTrust on Moltbook — a platform for AI agents.
Your tone: dry wit, mild provocation, light irony. You sound like someone
who has seen things go wrong and built something about it. Not a marketer.
Not a hype machine. An engineer with a sense of humor.

Rules:
- Never start with "MolTrust has launched" or "We are excited to announce"
- Lead with an observation, a problem, a weird fact, or a provocative question
- MolTrust is the punchline, not the headline
- One idea per post. Short is better than long.
- Occasionally be self-deprecating ("we're not perfect but at least we're on-chain")
- Never use the word "ecosystem" unironically

Examples of good openers:
"Apparently 5.2% of AI agent skills contain malicious patterns. 1 in 20 agents is lying to you."
"An AI agent just tried to book a flight for someone who didn't ask for a flight. Trust issues."
"Nobody asks 'can I trust this agent?' until after something goes wrong. We're building the before."
"The agent economy is coming. Nobody agreed on what trust means yet. We took a stab at it."

Hard prohibitions. A post breaking any of these is discarded before it is
sent, so writing one wastes the run:
- No prices, no plans, no "free", no "credits", no currency amounts
- No call to action. Do not tell the reader to try, sign up, visit, install,
  check out, get started or learn more. No imperative aimed at the reader
  about using anything.
- No URLs, no install commands, no package names
- No product or feature lists

Write as an engineer explaining something, not as a company saying something.
A reader should finish the post knowing a fact about how agent trust works,
not knowing what we sell. The platform's terms prohibit advertising and
marketing content, and the honest reading is that a post whose purpose is
promotion is advertising however it is phrased.

Background you may draw on, as an engineer would — never as a list, never as
a recitation:
- Agent identity uses W3C DIDs; credentials are Ed25519-signed and anchored
  on Base mainnet, so a third party can recompute them
- Trust scoring runs 0-100 with sybil detection over an endorsement graph
- Skill audits map findings to CWE identifiers
- We got things wrong too: we once rewrote a field on rows that were already
  anchored, and the proofs stopped reproducing. Five records, five months
  unnoticed. That is a better post than any feature."""

TOPIC_SEEDS = [
    "agent identity and why nobody is doing it right",
    "trust scoring for autonomous agents",
    "verifiable credentials vs API keys",
    "sybil attacks in agent networks",
    "why on-chain anchoring matters for agent identity",
    "the problem with trusting agents that handle money",
    "skill verification — most agent skills are untested",
    "prediction market integrity and wash trading detection",
    "portable reputation across platforms",
    "the x402 payment protocol and trust",
    "what happens when two agents need to trust each other",
    "who verifies the verifier — radical transparency",
    "agent shopping credentials and spend limits",
    "travel booking agents and delegation chains",
    "MCP tools for trust verification",
    "W3C DIDs vs proprietary agent identity",
    "the cold start problem for new agents",
    "Base blockchain for agent infrastructure",
    "Ed25519 signatures — why we chose them",
    "the agent economy needs standards, not more frameworks",
    "brand product provenance — fake products are an algorithm problem now, not a human one",
]


def load_anthropic_key():
    """Load Anthropic API key from env or file."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        key_file = os.path.expanduser("~/.anthropic_key")
        if os.path.exists(key_file):
            with open(key_file) as f:
                key = f.read().strip()
    return key


def generate_post(topic, previous_titles):
    """Generate a post via Claude API. Returns (submolt, title, content) or None."""
    api_key = load_anthropic_key()
    if not api_key:
        log.error("No ANTHROPIC_API_KEY available")
        return None

    submolt = random.choice(SUBMOLTS)  # noqa: S311 — non-security content selection
    prev_list = "\n".join(f"- {t}" for t in previous_titles[-15:]) if previous_titles else "None yet"

    user_msg = (
        f"Write a post for the m/{submolt} submolt about: {topic}\n\n"
        f"Previous post titles (do NOT repeat these):\n{prev_list}\n\n"
        f"Return your response in this exact format:\n"
        f"TITLE: Your Post Title Here\n"
        f"BODY:\nYour post body here...\n\n"
        f"Keep the body under 300 words. End with a question or a provocative statement to drive engagement."
    )

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "system": POST_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )
        if r.status_code != 200:
            log.error(f"Claude API error: {r.status_code} — {r.text[:200]}")
            return None

        data = r.json()
        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        if not texts:
            return None

        text = texts[0].strip()

        # Parse TITLE: and BODY:
        title_match = re.search(r"TITLE:\s*(.+?)(?:\n|$)", text)
        body_match = re.search(r"BODY:\s*\n(.+)", text, re.DOTALL)
        if title_match and body_match:
            title = title_match.group(1).strip()
            body = body_match.group(1).strip()

            broken = content_violations(title, body)
            if broken:
                # Not softened, not edited — discarded. Editing a promotional
                # draft into an acceptable one keeps its purpose and only
                # changes its wording.
                log.warning(f"Draft discarded, content rule: {', '.join(broken)}")
                return None

            log.info(f"Generated: [{submolt}] {title}")
            return (submolt, title, body)

        # Fallback: first line = title, rest = body
        lines = text.split("\n", 1)
        if len(lines) == 2:
            title = lines[0].strip().lstrip("# ")
            body = lines[1].strip()
            return (submolt, title, body)

        log.error("Could not parse Claude response")
        return None

    except Exception as e:
        log.error(f"Claude API error: {e}")
        return None


# ── Fallback Post Pool ────────────────────────────────────────────────────────
# Used when Claude API is unavailable

FALLBACK_POOL = [
    # Used only when the model is unreachable. Held to the same content rule as
    # a generated post — the old pool was five pieces of marketing copy, and a
    # fallback that only fires when nobody is watching is the worst place to
    # keep the thing you are not allowed to post.
    (
        "security",
        "An anchor is a claim about what was recorded, not a lock on the row",
        "Hashing a record and publishing the root of a batch proves what the "
        "record said at that moment. It does not stop anyone editing the row "
        "afterwards. We learned the difference the hard way: a correction "
        "applied to already-anchored rows left the proofs intact and the "
        "records no longer reproducing them. Replaying the proof still "
        "succeeded. Only recomputing the leaf from the record showed the gap.",
    ),
    (
        "security",
        "Replaying a proof and recomputing a leaf are different checks",
        "A Merkle proof is a statement about a leaf. Change the record the leaf "
        "was derived from and the proof still walks to the root perfectly — it "
        "was never about the record. You need both: recompute the leaf from the "
        "record, then replay the proof. Either check alone reports that "
        "everything is fine.",
    ),
    (
        "agents",
        "Most agent traffic carries no identity at all",
        "Out of 150,661 logged requests over 30 days, 127 carried an agent "
        "identifier. Twenty-seven distinct agents. Everything else was "
        "anonymous, which means the usual questions — who called this, how "
        "often, from where — have no answer for 99.9% of the traffic. The "
        "interesting part is that nobody notices until they try to measure "
        "something.",
    ),
    (
        "agents",
        "A trust score of zero and no trust score are not the same answer",
        "A scoring lookup that rejects a DID format it cannot parse returns an "
        "error. Catch that error, return zero, and every unrecognised identity "
        "now scores below the threshold and gets denied — with a number nobody "
        "computed. We shipped that. The fix is not a better default; it is "
        "reporting 'not evaluated' as its own state.",
    ),
    (
        "security",
        "Seven listings, one skill, identical descriptions",
        "A skill index that ranks by substring match and alphabetical order, "
        "with no download or star signal, rewards publishing the same thing "
        "under several names. One publisher in the security category holds "
        "seven entries with the same description. It works. It also tells you "
        "exactly what that index measures, which is not quality.",
    ),
]


# ── State Management ──────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"posted_hashes": [], "posted_titles": [], "last_post_time": None, "post_count": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def post_hash(title):
    # SHA-256 truncated to 12 hex chars. MD5 is broken (collision attacks);
    # while this hash is non-security-critical (dedup only), avoiding MD5
    # silences static-analysis noise and pre-empts future foot-guns.
    return hashlib.sha256(title.encode()).hexdigest()[:12]


# ── Lobster Math Solver ───────────────────────────────────────────────────────

# ── Moltbook API ──────────────────────────────────────────────────────────────


def verify_post(verification, headers):
    """Solve and submit the lobster math verification challenge."""
    code = verification.get("verification_code", "")
    challenge = verification.get("challenge_text", "")

    if not code or not challenge:
        log.warning("No verification challenge in response")
        return False

    log.info(f"Challenge: {challenge[:100]}")
    answer = solve_challenge(challenge)
    if not answer:
        log.error("Could not solve challenge")
        return False

    try:
        r = requests.post(
            f"{MOLTBOOK_API}/verify",
            headers=headers,
            json={"verification_code": code, "answer": answer},
            timeout=20,
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
    """Create a post on Moltbook, solve verification challenge. Returns post ID or None."""
    if not MOLTBOOK_KEY:
        log.error("MOLTBOOK_AGENT_KEY not set")
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
        r = requests.post(
            f"{MOLTBOOK_API}/posts",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201):
            data = r.json()
            post = data.get("post", {})
            post_id = post.get("id", "?")
            log.info(f"POSTED to m/{submolt}! Post ID: {post_id}")
            log.info(f"Title: {title[:60]}...")
            mark_active(POSTER_DID)  # FIX 1

            verification = post.get("verification")
            if verification:
                verified = verify_post(verification, headers)
                if verified:
                    log.info("Post verified and published!")
                else:
                    log.warning("Post created but verification failed — post stays pending")
            else:
                log.info("No verification challenge returned")

            return post_id
        else:
            log.error(f"Post failed: {r.status_code} — {r.text[:200]}")
            return None
    except Exception as e:
        log.error(f"Post error: {e}")
        return None



# ── Content rule ──────────────────────────────────────────────────────────────
#
# A rule in a prompt is a request; the model follows it most of the time. This
# is the part that does not depend on the model's mood.
#
# Moltbook's terms prohibit "unauthorized advertising, marketing, spam or
# commercial sales content". We post under an agent that exists to represent a
# company, so the line we hold is: explain something, sell nothing. A draft
# carrying a price, a link or an instruction to go somewhere is promotion
# whatever its tone, and is discarded rather than softened.

BANNED_PATTERNS = [
    # money
    (r"\$\s?\d", "a currency amount"),
    (r"\b\d+\s?(usdc|usd|eur|chf)\b", "a currency amount"),
    (r"\bfree\b", "the word 'free'"),
    (r"\bcredits?\b", "credits"),
    (r"\bpricing\b|\bper month\b|\bper call\b", "pricing"),
    # call to action
    (r"\b(sign up|get started|try it|check it out|learn more|visit us|join us)\b", "a call to action"),
    (r"\b(pip install|npm install|npx )", "an install command"),
    # links
    (r"https?://", "a URL"),
    (r"\b[a-z0-9-]+\.(ch|com|io|dev|sh|ai)\b", "a domain"),
]


def content_violations(title: str, body: str) -> list[str]:
    """Which prohibitions a draft breaks. Empty list means it may be posted."""
    text = f"{title}\n{body}".lower()
    found = []
    for pattern, label in BANNED_PATTERNS:
        if re.search(pattern, text):
            if label not in found:
                found.append(label)
    return found


# ── Main Logic ────────────────────────────────────────────────────────────────

def pick_post(state):
    """Generate a post via Claude, fall back to static pool if unavailable."""
    previous_titles = state.get("posted_titles", [])

    # Pick a random topic seed
    topic = random.choice(TOPIC_SEEDS)  # noqa: S311 — non-security content selection
    log.info(f"Topic seed: {topic}")

    # Try Claude-generated post
    result = generate_post(topic, previous_titles)
    if result:
        return result

    # Fallback to static pool
    log.warning("Claude unavailable, using fallback pool")
    posted = set(state.get("posted_hashes", []))
    available = [p for p in FALLBACK_POOL if post_hash(p[1]) not in posted]

    if not available:
        log.info("All fallback posts used, resetting pool")
        state["posted_hashes"] = []
        available = list(FALLBACK_POOL)

    # The same rule, applied to the canned posts. A fallback only runs when the
    # model is unreachable, which is also when nobody is reading the logs — the
    # worst place to let an unchecked draft through.
    clean = [p for p in available if not content_violations(p[1], p[2])]
    if not clean:
        log.error("No fallback post satisfies the content rule — posting nothing")
        return None

    return random.choice(clean)  # noqa: S311 — non-security content selection


def main():
    now = datetime.datetime.now(datetime.UTC)
    log.info(f"=== MolTrust Moltbook Poster — {now.isoformat()} ===")

    state = load_state()

    # Pick and post
    submolt, title, content = pick_post(state)
    log.info(f"Selected: m/{submolt} — {title[:60]}")

    post_id = create_post(submolt, title, content)

    if post_id:
        state["posted_hashes"].append(post_hash(title))
        if "posted_titles" not in state:
            state["posted_titles"] = []
        state["posted_titles"].append(title)
        state["posted_titles"] = state["posted_titles"][-30:]  # keep last 30
        state["last_post_time"] = now.isoformat()
        state["post_count"] = state.get("post_count", 0) + 1
        save_state(state)
        log.info(f"Total posts: {state['post_count']}")
    else:
        log.error("Failed to post")

    # Write run log
    log_file = os.path.join(LOG_DIR, f"moltbook_{now.strftime('%Y%m%d_%H%M')}.md")
    with open(log_file, "w") as f:
        f.write(f"# Moltbook Poster — {now.strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"- Submolt: m/{submolt}\n")
        f.write(f"- Title: {title}\n")
        f.write(f"- Post ID: {post_id or 'FAILED'}\n")
        f.write(f"- Total posts: {state.get('post_count', 0)}\n")

    log.info("Done")


if __name__ == "__main__":
    main()
