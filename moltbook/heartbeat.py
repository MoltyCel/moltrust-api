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

from agents.moltbook_poster import content_violations  # noqa: E402

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
        s["daily_date"] = today
        for k in ("upvoted", "commented", "welcomed"):
            s[k] = s[k][-200:]

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
    if COMMENT_POOL and state["daily_comments"] < 50 and (now - state["last_comment_ts"]) > 25:
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

    # Post original content every 2.5 hours
    hours_since_post = (now - state["last_post_ts"]) / 3600
    if POST_POOL and hours_since_post >= 2.5:
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

                save_state(state)

            except Exception as e:
                log.error(f"Tick error: {e}", exc_info=True)

            await asyncio.sleep(TICK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
