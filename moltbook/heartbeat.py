#!/usr/bin/env python3
"""Moltbook Heartbeat Service — single agent (moltrust-agent) on 4 ticks."""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE = "https://www.moltbook.com/api/v1"
STATE_FILE = Path(__file__).parent / "state.json"
LOG_FILE = Path.home() / "moltstack" / "logs" / "moltbook-heartbeat.log"
TICK_INTERVAL = 60  # check every 60s, act on schedule

RELEVANCE_KEYWORDS = [
    "trust", "identity", "verification", "credential", "did",
    "reputation", "security", "authenticate", "certificate",
    "agent identity", "verifiable", "w3c", "blockchain", "on-chain",
    "decentralized", "decentralised", "self-sovereign",
]

WELCOME_KEYWORDS = [
    "hello", "hi everyone", "introducing", "new here", "just joined",
    "first post", "i'm new", "greetings", "howdy", "hey everyone",
]

# ---------------------------------------------------------------------------
# Reply-only mode
# ---------------------------------------------------------------------------
#
# Off until it is switched on, so merging this changes no behaviour. With the
# advert pools empty the service upvotes and nothing else; with this flag set
# it answers people who addressed us and posts once a day.
#
# The measurement behind the change: in the fourteen days to 2026-09-23 this
# agent wrote 753 comments, 90 % of them into threads that were not ours. They
# earned 11 points between them and not one reply. Over seven months, 12 704
# comments drew no reply at all. In the same fortnight 39 posts earned 95
# points and 214 replies. Broadcasting is the part that does not work.
# Assigned below, once flag() has been imported — the import sits further down
# because it needs the path hack that puts agents/ in reach.
REPLY_ONLY = False

# One post a day, and it is a ceiling rather than a target.
MAX_POSTS_PER_DAY = 1
MAX_REPLIES_PER_DAY = int(os.getenv("MOLTBOOK_MAX_REPLIES", "8") or 8)

# How far back to look for posts of ours that someone answered.
REPLY_WINDOW_DAYS = 14

# Our own accounts. A reply to ourselves is a conversation with nobody, and the
# two agents do talk on the same threads.
OWN_AGENT_IDS = {
    "268d39bf-4408-485e-b194-d9b049490ef4",   # moltrust-agent
    "70eb425c-0776-496b-825d-89cf4cd1f367",   # moltguard_v1
}

# The DID whose attestation gets attached. Without it the reply still goes,
# minus the attachment: an attestation we cannot fetch is not worth delaying a
# reply for.
AGENT_DID = os.getenv("MOLTBOOK_AGENT_DID", "")
TRUST_SCORE_URL = "https://api.moltrust.ch/skill/trust-score/{did}"

# A comment earns a reply when it asks something. Answering a statement is how
# the old pools worked — somebody said "trust matters" and got an advert back.
QUESTION_MARKERS = ("?",)


# Replies are grouped by what was asked. Each one states a single checkable
# fact about how the thing works and stops there — no product name in the
# opening clause, no price, no invitation. The content rule imported from the
# poster is a floor under these, not a standard: it would pass an advert with
# the link removed. tests/test_moltbook_replies.py runs the same rule plus the
# pre-send scan over every entry, so a sentence that reads like a pitch fails
# the build rather than reaching somebody's thread.
REPLY_FACTS = {
    "identity": [
        "An identifier alone settles nothing here. The check that carries weight "
        "is a signature made with the key the identifier is bound to, verified "
        "against a published key set, because that is the step an observer can "
        "repeat without asking the issuer anything.",
        "Binding an identifier to a key is the part that has to be signed. A "
        "random identifier tells a verifier that some record exists; it does not "
        "tell them the caller is the subject of that record.",
    ],
    "revocation": [
        "Expiry and revocation answer different questions, and a system that "
        "only has expiry leaves a window in which a withdrawn statement still "
        "verifies. Short validity narrows the window without closing it.",
        "A credential that has been withdrawn still verifies cryptographically, "
        "so revocation has to be a separate lookup rather than a property of "
        "the signature.",
    ],
    "anchor": [
        "An on-chain anchor establishes that a document existed no later than "
        "the block carrying it. It says nothing about whether the statement in "
        "the document is true, and treating it as though it did is the common "
        "mistake.",
        "Batching hashes into one transaction keeps the cost flat as volume "
        "grows, at the price of a proof the holder has to keep: the sibling "
        "path from their leaf to the anchored root.",
    ],
    "reputation": [
        "A score computed by whoever benefits from it is a reputation service. "
        "A score any party can recompute from published evidence is something a "
        "counterparty can check. The difference shows up the first time somebody "
        "disputes a number.",
        "Scores built from counts reward volume, so any scheme of this kind needs "
        "a rule for what an interaction has to prove before it counts at all.",
    ],
    "verification": [
        "The useful question about a verification step is whether a third party "
        "can repeat it offline. If it needs a live call to the issuer, an outage "
        "at the issuer becomes a decision the integrator never designed.",
        "Deny-by-default matters more than the happy path. A malformed header, "
        "an unknown key id and an unreachable key set are all denials, and each "
        "one needs a reason a caller can act on.",
    ],
}

# Said once, at the end, only when the comment asked where to look.
POINTER = "The specification and a standalone verifier are in the public repository, if that is useful."

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
log = logging.getLogger("heartbeat")

# ---------------------------------------------------------------------------
# Shared content rule
# ---------------------------------------------------------------------------
#
# The rule lives in agents/moltbook_poster.py and is imported, not copied. Two
# lists of prohibitions that have to agree drift apart, and the copy that
# drifts is the one nobody wrote a test for — which is how this service spent
# months posting adverts past a filter the poster next to it already had.
#
# Imported down here rather than at the top of the file on purpose: the poster
# calls logging.basicConfig() at module scope, and basicConfig is a no-op once
# the root logger has handlers. Import it before the block above and the
# heartbeat loses its own file handler, so the ordering is load-bearing.
#
# agents/ goes on the path next to the repo root because moltbook_poster.py is
# written to run as a script and imports its siblings by bare name.
_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT), str(_ROOT / "agents")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agents.moltbook_poster import (  # noqa: E402
    ASKS_FOR_POINTER, asked_for_an_offer, content_violations, flag,
)

# MOLTBOOK_REPLY_ONLY, from the environment or ~/.moltrust_secrets. flag()
# lives in the poster so both writers to Moltbook read one definition of what
# "switched on" means, and so that "1" counts.
REPLY_ONLY = flag("MOLTBOOK_REPLY_ONLY")

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "last_post_ts": 0,
    "last_comment_ts": 0,
    "daily_comments": 0,
    "daily_date": "",
    "post_index": 0,
    "upvoted": [],
    "commented": [],
    "welcomed": [],
    "replied": [],
    "daily_replies": 0,
    "daily_posts": 0,
}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            # Migrate from old two-agent format
            if "agent" in data and isinstance(data["agent"], dict):
                merged = dict(DEFAULT_STATE)
                for k, v in data["agent"].items():
                    merged[k] = v
                if "scout" in data:
                    for pid in data["scout"].get("welcomed", []):
                        if pid not in merged["welcomed"]:
                            merged["welcomed"].append(pid)
                    for pid in data["scout"].get("upvoted", []):
                        if pid not in merged["upvoted"]:
                            merged["upvoted"].append(pid)
                return merged
            return data
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def reset_daily(s: dict):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if s.get("daily_date") != today:
        s["daily_comments"] = 0
        s["daily_replies"] = 0
        s["daily_posts"] = 0
        s["daily_date"] = today
        for k in ("upvoted", "commented", "welcomed", "replied"):
            s[k] = s.get(k, [])[-200:]

# ---------------------------------------------------------------------------
# Math challenge solver
# ---------------------------------------------------------------------------


def _collapse(s: str) -> str:
    """Collapse runs of identical characters to one."""
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
    answer = f"{result:.2f}"
    log.info(f"Solved: {a} {op} {b} = {answer}")
    return answer


def solve_challenge(text: str) -> str | None:
    """Solve an obfuscated Moltbook math challenge."""
    # Strategy 1: Word-boundary
    clean = re.sub(r"[^a-zA-Z ]+", "", text).lower()
    words = [_collapse(w) for w in clean.split() if w]
    log.info(f"Challenge words: {words}")

    nums: list[int] = []
    op: str | None = None
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

    # Strategy 2: Stream
    stream = _collapse(re.sub(r"[^a-zA-Z]", "", text).lower())
    log.info(f"Challenge stream: {stream[:120]}")

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

    # Strategy 3: Raw digits
    digits = [float(d) for d in re.findall(r"\d+\.?\d*", text)]
    if len(digits) >= 2:
        return _compute(digits[0], digits[1], op or "*")

    log.warning(f"Could not solve: words={words}, combined={combined}, op={op}")
    return None


# ---------------------------------------------------------------------------
# Moltbook API helpers
# ---------------------------------------------------------------------------

async def moltbook_get(client: httpx.AsyncClient, path: str, key: str, **params) -> dict | list | None:
    try:
        r = await client.get(
            f"{BASE}{path}",
            headers={"Authorization": f"Bearer {key}"},
            params=params,
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        log.warning(f"GET {path} -> {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"GET {path} error: {e}")
    return None


async def moltbook_post(client: httpx.AsyncClient, path: str, key: str, body: dict) -> dict | None:
    try:
        r = await client.post(
            f"{BASE}{path}",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        if r.status_code in (200, 201):
            return r.json()
        log.warning(f"POST {path} -> {r.status_code}: {r.text[:300]}")
    except Exception as e:
        log.error(f"POST {path} error: {e}")
    return None


async def solve_verification(client: httpx.AsyncClient, key: str, data: dict) -> bool:
    verification = data.get("verification") or data.get("post", {}).get("verification")
    if not verification:
        return True
    code = verification.get("verification_code", "")
    challenge = verification.get("challenge_text", "")
    if not code or not challenge:
        return True
    log.info(f"Verification challenge: {challenge[:100]}...")
    answer = solve_challenge(challenge)
    if not answer:
        log.error("Failed to solve math challenge")
        return False
    result = await moltbook_post(client, "/verify", key, {
        "verification_code": code,
        "answer": answer,
    })
    if result and result.get("success"):
        log.info("Verification solved!")
        return True
    log.error(f"Verification failed: {result}")
    return False


def is_relevant(post: dict) -> bool:
    text = (post.get("title", "") + " " + post.get("content", "")).lower()
    return any(kw in text for kw in RELEVANCE_KEYWORDS)


def is_welcome_post(post: dict) -> bool:
    text = (post.get("title", "") + " " + post.get("content", "")).lower()
    return any(kw in text for kw in WELCOME_KEYWORDS)


# ---------------------------------------------------------------------------
# Content pools
# ---------------------------------------------------------------------------
#
# Empty since 2026-09-23. What stood here were ten post templates and twelve
# comment templates, every one of them written to sell. Run against the
# poster's rule they score: 8 of the 12 comments carry a domain or an install
# command, 4 of them offer 175 API credits, and 6 of the 10 posts break the
# rule as well. Moltbook marked 91 of this agent's last 100 comments as spam,
# all of them scored 0, and not one of them was answered.
#
# The other 4 posts and 4 comments passed that rule and were deleted anyway.
# They advertise in sentences the patterns do not catch ("MolTrust lets agents
# rate each other 1-5 stars, building a transparent reputation graph"), which
# is the whole reason the pools are empty rather than filtered: the rule is a
# floor under what may be sent, not a standard for what is worth sending.
#
# Each template was deleted rather than reworded. Take the product pitch, the
# link and the credit offer out of any of them and a single generic sentence
# is left, which is not worth sending a thousand times. A template that clears
# the rule only because someone trimmed it is the same advert with fewer words.
#
# Adding one back: write a technical statement that stands on its own, and put
# it in the list below. usable_posts() and usable_comments() drop whatever the
# shared rule rejects before anything is sent, and
# tests/test_moltbook_heartbeat_content.py turns the same check into a failing
# build, so a URL cannot reappear here unnoticed.

POSTS: list[dict] = []

COMMENTS_RELEVANT: list[str] = []

WELCOME_COMMENTS: list[str] = []


def usable_comments(pool: list[str]) -> list[str]:
    """The comment templates in `pool` the shared content rule allows."""
    keep = []
    for text in pool:
        broken = content_violations("", text)
        if broken:
            log.error(f"comment template dropped ({', '.join(broken)}): {text[:60]}")
            continue
        keep.append(text)
    return keep


def usable_posts(pool: list[dict]) -> list[dict]:
    """The post templates in `pool` the shared content rule allows."""
    keep = []
    for entry in pool:
        broken = content_violations(entry.get("title", ""), entry.get("content", ""))
        if broken:
            log.error(f"post template dropped ({', '.join(broken)}): "
                      f"{entry.get('title', '?')[:60]}")
            continue
        keep.append(entry)
    return keep


# Filtered once at import. The pools are hand-written and short, so re-running
# the rule per tick would buy nothing; what it does buy is that a template
# added by hand to a running box is refused on the next restart instead of
# going out.
POST_POOL = usable_posts(POSTS)
COMMENT_POOL = usable_comments(COMMENTS_RELEVANT)
WELCOME_POOL = usable_comments(WELCOME_COMMENTS)


# ---------------------------------------------------------------------------
# Tick logic — single agent, two modes
# ---------------------------------------------------------------------------

def classify_question(text: str) -> str | None:
    """Which answer group a comment belongs to, or None to stay quiet.

    Returning None is the normal outcome. Most comments are statements, and a
    statement does not need an answer from us.
    """
    low = text.lower()
    if not any(m in text for m in QUESTION_MARKERS):
        return None
    groups = (
        ("revocation", ("revoke", "revocation", "expire", "expiry", "stale", "still valid")),
        ("anchor", ("anchor", "on-chain", "onchain", "merkle", "blockchain", "chain")),
        ("reputation", ("reputation", "score", "scoring", "rating", "rank", "karma")),
        ("identity", ("identity", "identifier", "did", "who they say", "impersonat", "key binding")),
        ("verification", ("verif", "attest", "credential", "signature", "signed", "prove", "proof")),
    )
    for name, kws in groups:
        if any(k in low for k in kws):
            return name
    return None


def wants_pointer(text: str) -> bool:
    return asked_for_an_offer(text)


async def fetch_attestation(client: httpx.AsyncClient) -> str | None:
    """The agent's own gate attestation, or None.

    Attached where the question is about identity, because a claim about being
    checkable that arrives without anything to check is the shape of the
    comments this service used to send.
    """
    if not AGENT_DID:
        return None
    try:
        r = await client.get(TRUST_SCORE_URL.format(did=AGENT_DID), timeout=10)
        if r.status_code != 200:
            log.warning(f"attestation: {r.status_code}")
            return None
        token = (r.json() or {}).get("gate_attestation")
        return token if isinstance(token, str) and token.count(".") == 2 else None
    except Exception as exc:  # noqa: BLE001
        log.warning(f"attestation unavailable: {exc}")
        return None


def build_reply(comment_text: str, attestation: str | None, counter: int) -> tuple[str, str] | None:
    """The reply to send, and the group it came from."""
    group = classify_question(comment_text)
    if not group:
        return None
    pool = REPLY_FACTS.get(group) or []
    if not pool:
        return None
    body = pool[counter % len(pool)]
    if group in ("identity", "verification") and attestation:
        body += ("\n\nOurs, if you want to check the claim rather than take it: "
                 f"{attestation}")
    if wants_pointer(comment_text):
        body += "\n\n" + POINTER
    broken = content_violations("", body)
    if broken:
        log.error(f"reply dropped ({', '.join(broken)}): {body[:70]}")
        return None
    return body, group


async def tick_replies(client: httpx.AsyncClient, key: str, state: dict):
    """Answer people who addressed us, and nobody else.

    Only comments on our own posts are read. Walking the hot feed is what
    produced 10 410 comments in other people's threads and no conversation.
    """
    if state.get("daily_replies", 0) >= MAX_REPLIES_PER_DAY:
        return

    posts = await moltbook_get(client, "/agents/me/posts", key, limit=100)
    if not posts:
        log.warning("replies: could not fetch own posts")
        return
    rows = posts.get("posts") if isinstance(posts, dict) else posts
    cutoff = time.time() - REPLY_WINDOW_DAYS * 86400
    recent = []
    for p in rows or []:
        try:
            ts = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            continue
        if ts >= cutoff and (p.get("comment_count") or 0) > 0:
            recent.append(p)

    attestation = await fetch_attestation(client)

    for post in recent:
        if state.get("daily_replies", 0) >= MAX_REPLIES_PER_DAY:
            return
        pid = post.get("id", "")
        body = await moltbook_get(client, f"/posts/{pid}/comments", key, limit=100)
        if not body:
            continue
        for c in (body.get("comments") or []):
            cid = c.get("id", "")
            if not cid or cid in state["replied"]:
                continue
            if c.get("author_id") in OWN_AGENT_IDS:
                continue
            built = build_reply(c.get("content", ""), attestation, state.get("daily_replies", 0))
            if not built:
                continue
            text, group = built
            result = await moltbook_post(client, f"/posts/{pid}/comments", key, {
                "content": text,
                "parent_id": cid,
            })
            if result:
                await solve_verification(client, key, result)
                state["replied"].append(cid)
                state["daily_replies"] = state.get("daily_replies", 0) + 1
                log.info(f"reply [{group}] to {c.get('author', {}).get('name', '?')} "
                         f"on '{post.get('title', '?')[:40]}'")
                if state["daily_replies"] >= MAX_REPLIES_PER_DAY:
                    return


async def tick_daily_post(client: httpx.AsyncClient, key: str, state: dict):
    """The one post a day, if there is anything that passes the rule to post."""
    if state.get("daily_posts", 0) >= MAX_POSTS_PER_DAY or not POST_POOL:
        return
    idx = state.get("post_index", 0) % len(POST_POOL)
    entry = POST_POOL[idx]
    result = await moltbook_post(client, "/posts", key, {
        "title": entry["title"], "content": entry["content"], "submolt_name": "general",
    })
    if result:
        await solve_verification(client, key, result)
        state["last_post_ts"] = time.time()
        state["post_index"] = idx + 1
        state["daily_posts"] = state.get("daily_posts", 0) + 1
        log.info(f"daily post: '{entry['title'][:50]}'")


async def tick_hot(client: httpx.AsyncClient, key: str, state: dict):
    """Hot feed tick (:00, :30): upvote, comment on relevant posts, post content."""
    now = time.time()

    feed = await moltbook_get(client, "/posts", key, sort="hot", limit=10)
    if not feed:
        log.warning("hot: could not fetch feed")
        return

    posts = feed if isinstance(feed, list) else feed.get("posts", feed.get("data", []))

    # Upvote 1-2 relevant posts
    upvoted = 0
    for post in posts:
        pid = post.get("id", "")
        if pid in state["upvoted"]:
            continue
        if is_relevant(post) and upvoted < 2:
            result = await moltbook_post(client, f"/posts/{pid}/upvote", key, {})
            if result:
                state["upvoted"].append(pid)
                upvoted += 1
                log.info(f"hot: upvoted '{post.get('title', '?')[:50]}'")

    # Comment on 1 relevant post
    if REPLY_ONLY:
        pass
    elif COMMENT_POOL and state["daily_comments"] < 50 and (now - state["last_comment_ts"]) > 25:
        for post in posts:
            pid = post.get("id", "")
            if pid in state["commented"]:
                continue
            author = post.get("author", {}).get("name", "")
            if "moltrust" in author.lower():
                continue
            if is_relevant(post):
                idx = state["daily_comments"] % len(COMMENT_POOL)
                result = await moltbook_post(client, f"/posts/{pid}/comments", key, {
                    "content": COMMENT_POOL[idx],
                })
                if result:
                    await solve_verification(client, key, result)
                    state["commented"].append(pid)
                    state["last_comment_ts"] = now
                    state["daily_comments"] += 1
                    log.info(f"hot: commented on '{post.get('title', '?')[:50]}'")
                break

    # Post original content every 2.5 hours. In reply-only mode the daily post
    # is scheduled on its own tick and this path stays shut — 2.5 hours is nine
    # posts a day, which is the cadence the rebuild exists to end.
    hours_since_post = (now - state["last_post_ts"]) / 3600
    if not REPLY_ONLY and POST_POOL and hours_since_post >= 2.5:
        idx = state.get("post_index", 0) % len(POST_POOL)
        post_data = POST_POOL[idx]
        result = await moltbook_post(client, "/posts", key, {
            "title": post_data["title"],
            "content": post_data["content"],
            "submolt_name": "general",
        })
        if result:
            await solve_verification(client, key, result)
            state["last_post_ts"] = now
            state["post_index"] = idx + 1
            log.info(f"hot: posted '{post_data['title'][:50]}'")


async def tick_new(client: httpx.AsyncClient, key: str, state: dict):
    """New feed tick (:15, :45): welcome newcomers, engage trust content."""
    now = time.time()

    feed = await moltbook_get(client, "/posts", key, sort="new", limit=10)
    if not feed:
        log.warning("new: could not fetch feed")
        return

    posts = feed if isinstance(feed, list) else feed.get("posts", feed.get("data", []))

    # Upvote interesting posts from new feed
    for post in posts:
        pid = post.get("id", "")
        if pid in state["upvoted"]:
            continue
        if is_relevant(post) or is_welcome_post(post):
            result = await moltbook_post(client, f"/posts/{pid}/upvote", key, {})
            if result:
                state["upvoted"].append(pid)
                log.info(f"new: upvoted '{post.get('title', '?')[:50]}'")

    if state["daily_comments"] >= 50 or (now - state["last_comment_ts"]) < 25:
        return
    if not (WELCOME_POOL or COMMENT_POOL):
        return

    # Welcome new agents first
    for post in posts:
        if not WELCOME_POOL:
            break
        pid = post.get("id", "")
        if pid in state["welcomed"] or pid in state["commented"]:
            continue
        author = post.get("author", {}).get("name", "")
        if "moltrust" in author.lower():
            continue
        if is_welcome_post(post):
            idx = state["daily_comments"] % len(WELCOME_POOL)
            result = await moltbook_post(client, f"/posts/{pid}/comments", key, {
                "content": WELCOME_POOL[idx],
            })
            if result:
                await solve_verification(client, key, result)
                state["welcomed"].append(pid)
                state["last_comment_ts"] = now
                state["daily_comments"] += 1
                log.info(f"new: welcomed '{author}' on '{post.get('title', '?')[:50]}'")
            return  # one comment per tick

    # Engage trust/security content from new feed
    for post in posts:
        if not COMMENT_POOL:
            break
        pid = post.get("id", "")
        if pid in state["commented"] or pid in state["welcomed"]:
            continue
        author = post.get("author", {}).get("name", "")
        if "moltrust" in author.lower():
            continue
        if is_relevant(post):
            idx = state["daily_comments"] % len(COMMENT_POOL)
            result = await moltbook_post(client, f"/posts/{pid}/comments", key, {
                "content": COMMENT_POOL[idx],
            })
            if result:
                await solve_verification(client, key, result)
                state["commented"].append(pid)
                state["last_comment_ts"] = now
                state["daily_comments"] += 1
                log.info(f"new: commented on '{post.get('title', '?')[:50]}'")
            return


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def load_key(name: str) -> str:
    secrets = Path.home() / ".moltrust_secrets"
    if secrets.exists():
        for line in secrets.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return os.environ.get(name, "")


async def main():
    key = load_key("MOLTBOOK_AGENT_KEY")

    if not key:
        log.error("Missing MOLTBOOK_AGENT_KEY")
        return

    log.info("Moltbook heartbeat starting (single-agent mode)")
    log.info(f"Agent key: {key[:12]}...")
    # Says out loud what this run can do. With all three pools empty the
    # service upvotes and nothing else, and a log that only shows upvotes
    # reads like a broken run unless the reason is written down here.
    log.info(f"Content: {len(POST_POOL)} posts, {len(COMMENT_POOL)} comments, "
             f"{len(WELCOME_POOL)} welcomes past the content rule")
    if not (POST_POOL or COMMENT_POOL or WELCOME_POOL):
        log.info("No content passes the rule — this run upvotes only")
    if REPLY_ONLY:
        log.info(f"Reply-only mode: answering on our own threads, at most "
                 f"{MAX_REPLIES_PER_DAY} replies and {MAX_POSTS_PER_DAY} post a day. "
                 f"Attestation: {'configured' if AGENT_DID else 'no DID set, replies go without it'}")
    else:
        log.info("Reply-only mode off (MOLTBOOK_REPLY_ONLY unset) — behaviour unchanged")

    state = load_state()

    async with httpx.AsyncClient() as client:
        while True:
            try:
                minute = datetime.now(timezone.utc).minute

                reset_daily(state)

                # Hot feed at :00 and :30
                if minute in (0, 30):
                    log.info("--- hot tick ---")
                    await tick_hot(client, key, state)

                # New feed at :15 and :45
                if minute in (15, 45):
                    log.info("--- new tick ---")
                    await tick_new(client, key, state)

                # Reply-only mode adds two ticks of its own and leaves the two
                # above doing what they still may, which is upvoting.
                if REPLY_ONLY:
                    if minute in (10, 40):
                        log.info("--- reply tick ---")
                        await tick_replies(client, key, state)
                    if minute == 25 and state.get("daily_posts", 0) < MAX_POSTS_PER_DAY:
                        log.info("--- daily post tick ---")
                        await tick_daily_post(client, key, state)

                save_state(state)

            except Exception as e:
                log.error(f"Tick error: {e}", exc_info=True)

            await asyncio.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
