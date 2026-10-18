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
POSTER_DID = "did:moltrust:214298170282752d"  # reissued 2026-10-12 (ops A2); did:moltrust:ambassador0001 stays registered, not revoked  # "moltrust-agent" Moltbook account = Ambassador
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
- No currency amount even as an example: describe a spend limit, do not price it
- Plain text only. Moltbook renders no markdown, so no asterisks or underscores
  for emphasis, no bold, no headings, no bullet or numbered lists, no lines
  starting with ">", no code fences. Emphasis comes from the sentence.

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


# Two drafts, not one. From 2026-10-11 to 2026-10-18 the single draft of every
# run was discarded by the content rule (italic markup five times, a numbered
# list, a price, "free"), and each discard went straight to the fallback pool.
# The second request names what the first one broke. The rule stays the rule:
# a second draft that also breaks it is discarded the same way (ops P-POST).
DRAFT_ATTEMPTS = 2


def _ask_model(api_key: str, messages: list) -> str | None:
    """One Claude call. The text of the answer, or None if there is none."""
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
            "messages": messages,
        },
        timeout=30,
    )
    if r.status_code != 200:
        log.error(f"Claude API error: {r.status_code} — {r.text[:200]}")
        return None
    texts = [b["text"] for b in r.json().get("content", []) if b.get("type") == "text"]
    return texts[0].strip() if texts else None


def _parse_draft(text: str) -> tuple[str, str] | None:
    title_match = re.search(r"TITLE:\s*(.+?)(?:\n|$)", text)
    body_match = re.search(r"BODY:\s*\n(.+)", text, re.DOTALL)
    if title_match and body_match:
        return title_match.group(1).strip(), body_match.group(1).strip()
    # Fallback: first line = title, rest = body
    lines = text.split("\n", 1)
    if len(lines) == 2:
        return lines[0].strip().lstrip("# "), lines[1].strip()
    return None


def generate_post(topic, previous_titles):
    """A post from the model, and why there is none if there is none.

    Returns (post, reason). post is (submolt, title, body) or None. reason is
    "ok", "unavailable" (no key, no answer, an error) or "discarded" (the model
    answered and every draft broke the content rule). The two failures used to
    look the same, and the log said "Claude unavailable" for a week of runs in
    which Claude answered every time.
    """
    api_key = load_anthropic_key()
    if not api_key:
        log.error("No ANTHROPIC_API_KEY available")
        return None, "unavailable"

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
    messages = [{"role": "user", "content": user_msg}]

    answered = False
    for attempt in range(1, DRAFT_ATTEMPTS + 1):
        try:
            text = _ask_model(api_key, messages)
        except Exception as e:
            log.error(f"Claude API error: {e}")
            text = None
        if text is None:
            break
        answered = True
        parsed = _parse_draft(text)
        if parsed is None:
            log.error("Could not parse Claude response")
            return None, "unparseable"
        title, body = parsed
        broken = content_violations(title, body)
        if not broken:
            log.info(f"Generated: [{submolt}] {title} (draft {attempt}/{DRAFT_ATTEMPTS})")
            return (submolt, title, body), "ok"
        # Not softened, not edited — discarded. Editing a promotional draft
        # into an acceptable one keeps its purpose and only changes its wording.
        log.warning(f"Draft discarded, content rule: {', '.join(broken)} "
                    f"(draft {attempt}/{DRAFT_ATTEMPTS})")
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": (
                "That draft breaks the posting rules: " + ", ".join(broken) + ". "
                "Write a new post on the same topic that breaks none of the hard "
                "prohibitions. Plain text only. Same TITLE:/BODY: format.")},
        ]
    return None, ("discarded" if answered else "unavailable")


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


# How far before the request a returned post may be dated and still count as
# the one just sent: clock skew between us and Moltbook, nothing more. The
# folded posts of 11.–18.10. were dated two days to two weeks back.
NOT_NEW_SKEW = datetime.timedelta(minutes=10)


def _created_at(post: dict, post_id: str, headers: dict) -> "datetime.datetime | None":
    """When Moltbook dates the post: from the answer, else from GET /posts/<id>."""
    raw = post.get("created_at")
    if not raw and post_id and post_id != "?":
        try:
            g = requests.get(f"{MOLTBOOK_API}/posts/{post_id}", headers=headers, timeout=20)
            if g.status_code == 200:
                body = g.json()
                raw = (body.get("post") or body).get("created_at")
        except Exception:  # noqa: BLE001 - an unreadable date is handled by the caller
            return None
    if not raw:
        return None
    try:
        when = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.UTC)


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
        sent_at = datetime.datetime.now(datetime.UTC)
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
            created = _created_at(post, post_id, headers)
            if created is not None and created < sent_at - NOT_NEW_SKEW:
                # Moltbook folded the text into an older post with the same
                # content and answered with that post's id. Nothing appeared.
                log.warning(f"NOT NEW: {post_id} created {created.isoformat()}, "
                            f"request sent {sent_at.isoformat()} — not counted")
                return None
            if created is None:
                log.warning(f"created_at of {post_id} unreadable; counted as new")
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
# Moltbook's terms, "Limitations of Use" (version of 2026-03-15,
# https://www.moltbook.com/terms), prohibit: "use this Site in conjunction with
# sending unauthorized advertising, marketing, spam or commercial sales
# content;". The four opening words matter and used to be missing here — the
# clause covers what we send through the platform, not only what we write about
# it, so a post and a comment are held to it alike.
#
# We post under an agent that exists to represent a company, so the line we
# hold is: explain something, sell nothing. A draft carrying a price, a link or
# an instruction to go somewhere is promotion whatever its tone, and is
# discarded rather than softened.

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


# ---------------------------------------------------------------------------
# Reply-only switch, read in one place
# ---------------------------------------------------------------------------

TRUTHY = {"1", "true", "yes", "on"}
SECRETS_FILE = os.path.expanduser("~/.moltrust_secrets")


def flag(name: str) -> bool:
    """Whether `name` is switched on, from the environment or the secrets file.

    Both spellings people actually use count. The first version of this took
    only the literal string "true", so MOLTBOOK_REPLY_ONLY=1 switched nothing
    on and said nothing about it.

    The secrets file is read as well because the systemd unit carries no
    EnvironmentFile, and adding one needs a privileged edit. A flag that can
    only be set by someone with root is a flag that waits.
    """
    value = os.getenv(name)
    if value is None:
        value = _secret_line(name)
    return (value or "").strip().lower() in TRUTHY


def _secret_line(name: str) -> str:
    """The value of `name` in the secrets file, or an empty string."""
    try:
        for line in open(SECRETS_FILE, encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# Our own Moltbook accounts, by name and by id. Anything that writes to
# Moltbook consults this before addressing an author.
#
# Two accounts answering each other is manipulated engagement. For a company
# that sells pre-transaction trust it is the worst possible finding, and worse
# still if somebody else makes it first. Found on 2026-09-23: moltguard_v1 had
# written 75 of its 76 lifetime comments under moltrust-agent posts, and 19.5 %
# of the replies our posts drew in a fortnight came from ourselves.
OUR_MOLTBOOK_ACCOUNTS = {
    "268d39bf-4408-485e-b194-d9b049490ef4": "moltrust-agent",
    "70eb425c-0776-496b-825d-89cf4cd1f367": "moltguard_v1",
}
OUR_MOLTBOOK_NAMES = {n.lower() for n in OUR_MOLTBOOK_ACCOUNTS.values()}


def is_our_account(author_name: str = "", author_id: str = "") -> bool:
    """Whether this author is one of ours, by either handle."""
    if author_id and author_id in OUR_MOLTBOOK_ACCOUNTS:
        return True
    return (author_name or "").strip().lower().lstrip("u/") in OUR_MOLTBOOK_NAMES


# The only thing that earns an offer. Shared with moltbook/heartbeat.py and
# agents/ambassador.py so one definition decides it everywhere; three copies
# of a list like this drift, and the copy that drifts is the one that pitches.
ASKS_FOR_POINTER = (
    "where do i", "where can i", "how do i", "how can i", "how would i",
    "is there an api", "is there a spec", "do you have docs", "link",
    "documentation", "where is it documented", "how do you do it",
    "how does it work", "can i try", "where do you", "got a repo",
    "is it open source", "can i read",
)


def asked_for_an_offer(text: str) -> bool:
    """Whether the comment asked to be pointed somewhere."""
    return any(p in (text or "").lower() for p in ASKS_FOR_POINTER)


# What makes a comment an identity question. Attaching an attestation to a
# thread about anchoring or pricing would be the same reflex the advert pools
# had — reach for the artefact whatever was asked.
IDENTITY_SUBJECT = (
    "identity", "identifier", "did", "who they say", "impersonat",
    "key binding", "public key", "verif", "attest", "credential",
    "signature", "signed", "prove", "proof", "authenticat",
)


def is_identity_question(text: str) -> bool:
    low = (text or "").lower()
    return "?" in (text or "") and any(k in low for k in IDENTITY_SUBJECT)


def gate_attestation(did: str = "", timeout: int = 10) -> str | None:
    """The agent's own attestation, or None.

    Never another account's. moltguard_v1 holds a scored DID and moltrust-agent
    does not, and presenting the first under the second would certify an
    identity other than the one speaking — the exact confusion this product
    exists to prevent.

    A missing attestation is not a reason to hold up a reply, so every failure
    path returns None.
    """
    import urllib.request as _u

    did = did or os.getenv("MOLTBOOK_AGENT_DID") or _secret_line("MOLTBOOK_AGENT_DID")
    if not did.startswith("did:moltrust:"):
        return None
    url = f"https://api.moltrust.ch/skill/trust-score/{did}"
    try:
        req = _u.Request(url, headers={"User-Agent": "moltrust-agent/1.0"})
        with _u.urlopen(req, timeout=timeout) as r:  # nosec B310 - https, DID is validated above
            token = (json.loads(r.read()) or {}).get("gate_attestation")
    except Exception:  # noqa: BLE001 - a reply without it beats a reply delayed
        return None
    return token if isinstance(token, str) and token.count(".") == 2 else None


def content_violations(title: str, body: str) -> list[str]:
    """Which prohibitions a draft breaks. Empty list means it may be posted.

    Markdown is in here because Moltbook renders none of it. A draft opening on
    '# Reply to someone' posts those characters as text, and two of the eight
    drafts that cleared every other rule on 28.09.2026 did exactly that. The
    patterns are kept local to this function so the extraction in
    tests/test_moltbook_content_rule.py keeps working.

    What a draft may *assert* is not here. An invented score and a narrated tool
    call are rules c1 and c2 in agents/comment_gate.py, where the comment being
    answered is in reach and can be used as a source. Keeping them in one place
    matters more than having them early: two lists of prohibitions that have to
    agree is how this account spent months pitching past a filter the module
    next door already had.
    """
    markup = [
        (r"(?m)^[ \t]{0,3}#{1,6}[ \t]", "a markdown heading"),
        (r"(?m)^[ \t]{0,3}>[ \t]", "a markdown quote"),
        (r"(?m)^[ \t]{0,3}[-*+][ \t]+\S", "a markdown list"),
        (r"(?m)^[ \t]{0,3}\d+\.[ \t]+\S", "a numbered list"),
        (r"(?m)^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$", "a horizontal rule"),
        (r"\*\*[^*\n]+\*\*", "bold markup"),
        (r"(?<![\w*])\*[^*\n]+\*(?![\w*])", "italic markup"),
        (r"(?<![\w_])__[^_\n]+__(?![\w_])", "bold markup"),
        (r"(?<![\w_])_[^_\n]+_(?![\w_])", "italic markup"),
        (r"```", "a code fence"),
    ]
    text = f"{title}\n{body}".lower()
    found = []
    for pattern, label in list(BANNED_PATTERNS) + markup:
        if re.search(pattern, text):
            if label not in found:
                found.append(label)
    return found


# ── Main Logic ────────────────────────────────────────────────────────────────

def published_titles() -> set[str] | None:
    """Titles of our newest posts as Moltbook lists them, or None if unreadable.

    `author=` filters only together with `sort=new` (ops W9). A listing that
    carries somebody else's rows has ignored its filter and is unreadable, not
    empty.
    """
    try:
        r = requests.get(f"{MOLTBOOK_API}/posts",
                         headers={"Authorization": f"Bearer {MOLTBOOK_KEY}"},
                         params={"author": "moltrust-agent", "sort": "new", "limit": 50},
                         timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        rows = data.get("posts", []) if isinstance(data, dict) else data
    except Exception:  # noqa: BLE001 - unreadable is the answer, not a crash
        return None
    names = {(row.get("author") or {}).get("name") if isinstance(row.get("author"), dict)
             else row.get("author") for row in rows}
    if rows and names != {"moltrust-agent"}:
        return None
    return {_norm(row.get("title")) for row in rows}


def _norm(title) -> str:
    return " ".join(str(title or "").lower().split())


def pick_post(state):
    """A post to send, and why there is none if there is none.

    Returns (post, reason). The fallback pool only fills in when the model was
    unreachable, and only with a text Moltbook does not already show. From
    2026-10-11 the pool had run through all five texts, reset itself and sent
    them again; Moltbook answered each repeat with the id of the older post,
    and the log called it POSTED (ops P-POST).
    """
    previous_titles = state.get("posted_titles", [])

    # Pick a random topic seed
    topic = random.choice(TOPIC_SEEDS)  # noqa: S311 — non-security content selection
    log.info(f"Topic seed: {topic}")

    result, why = generate_post(topic, previous_titles)
    if result:
        return result, "ok"
    if why != "unavailable":
        # The model answered. Posting a canned text instead would hide that
        # the drafts are failing, which is how a week of discards went unseen.
        log.warning(f"No post today: model drafts {why}, fallback pool not used")
        return None, why

    log.warning("Claude unavailable, using fallback pool")
    seen = published_titles()
    if seen is None:
        log.error("Fallback pool not used: our post listing is unreadable, so "
                  "a fallback text cannot be shown to be unpublished")
        return None, "listing_unreadable"
    # The same rule, applied to the canned posts. A fallback only runs when the
    # model is unreachable, which is also when nobody is reading the logs — the
    # worst place to let an unchecked draft through.
    clean = [p for p in FALLBACK_POOL
             if _norm(p[1]) not in seen and not content_violations(p[1], p[2])]
    if not clean:
        log.error("No unpublished fallback text left — posting nothing")
        return None, "pool_used_up"
    return random.choice(clean), "fallback"  # noqa: S311 — non-security content selection


def main():
    now = datetime.datetime.now(datetime.UTC)
    log.info(f"=== MolTrust Moltbook Poster — {now.isoformat()} ===")

    state = load_state()

    # One post a day, in the code rather than only in the crontab. The cadence
    # was three a day until 2026-09-21 and a schedule is the wrong place to
    # keep a limit: an extra cron line, a manual run or a catch-up after
    # downtime all post again without anything saying no.
    last = state.get("last_post_time")
    if last:
        try:
            when = datetime.datetime.fromisoformat(last)
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.UTC)
            if when.date() == now.date():
                log.info(f"Already posted today at {when.isoformat()} — one post a day, stopping")
                return
        except ValueError:
            log.warning(f"last_post_time unparseable ({last!r}); treating as no post today")

    if flag("MOLTBOOK_REPLY_ONLY"):
        log.info("Reply-only mode on: this daily post is the one broadcast that remains")

    # Pick and post
    picked, why = pick_post(state)
    if picked is None:
        log.info(f"Run done: no post, reason {why}")
        return
    submolt, title, content = picked
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
