#!/usr/bin/env python3
"""MolTrust Ambassador Agent — Auto-reply to comments on our Moltbook posts.

Fully automated: detects new comments, generates a reply via Claude, posts it.
Uses workspace bootstrap pattern for identity, personality, and memory.

Usage:
    ambassador.py run     — Check for new comments and reply automatically
    ambassador.py status  — Print stats to stdout
    ambassador.py post    — Generate and post a new topic to m/agenttrust
"""

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from lib.moltbook_verify import solve_challenge  # LLM multi-step verify solver

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"
OUR_AUTHOR = "moltrust-agent"
from activity import mark_active, ambassador_did  # FIX 1: un-ghost on post

# One definition of the content rule and of what counts as being asked, shared
# with moltbook/heartbeat.py. Two lists of prohibitions that have to agree is
# how this account spent months pitching past a filter the module next door
# already had.
from moltbook_poster import (
    asked_for_an_offer, content_violations, gate_attestation, is_identity_question,
    is_our_account,
)

# The comment gate: the spam rate, the daily cap and the relevance bar, all
# read before anything is written. reply_radar supplies the published pages so
# a figure in a comment can be shown to come from somewhere.
from app import notify
from agents import comment_gate, reply_radar, voice_gate
# One place for this DID: see agents/activity.py. A counter keyed on the
# literal reads zero the day it is re-issued.
AMBASSADOR_DID = ambassador_did()

STATE_FILE = Path.home() / ".ambassador_state.json"
LOG_FILE = Path.home() / "moltstack" / "logs" / "ambassador.log"

# Workspace paths
WORKSPACE = Path.home() / "moltstack" / "agents" / "workspace" / "ambassador"
WS_IDENTITY = WORKSPACE / "IDENTITY.md"
WS_SOUL = WORKSPACE / "SOUL.md"
WS_RULES = WORKSPACE / "RULES.md"
WS_MEMORY = WORKSPACE / "MEMORY.md"
WS_HEARTBEAT = WORKSPACE / "HEARTBEAT.md"
WS_TOOLS = WORKSPACE / "TOOLS.md"
WS_LOGS = WORKSPACE / "logs"

# Rate limits
AGENT_REPLY_LIMIT_24H = 3  # max replies to same agent in 24h

# ---------------------------------------------------------------------------
# Logging — stdout only, cron redirect handles file output
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
    ],
)
# httpx logs every request URL at INFO, which writes the Telegram bot token
# into the log file in clear text. Keep it at WARNING.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ambassador")

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


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


MOLTBOOK_KEY = ""
ANTHROPIC_KEY = ""


def init_keys():
    global MOLTBOOK_KEY, ANTHROPIC_KEY
    MOLTBOOK_KEY = load_key("MOLTBOOK_AGENT_KEY")

    ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_KEY:
        key_file = Path.home() / ".anthropic_key"
        if key_file.exists():
            ANTHROPIC_KEY = key_file.read_text().strip()


# ---------------------------------------------------------------------------
# Workspace Bootstrap Loader
# ---------------------------------------------------------------------------


def _read_ws(path: Path) -> str:
    """Read a workspace file, return empty string if missing."""
    if path.exists():
        return path.read_text().strip()
    return ""


def _today_log_path() -> Path:
    """Return path to today's workspace log file."""
    return WS_LOGS / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"


def load_bootstrap() -> str:
    """Load core bootstrap context: IDENTITY + SOUL + RULES + today's log.
    Always loaded into the system prompt. Budget: ~2000 tokens."""
    parts = []

    identity = _read_ws(WS_IDENTITY)
    if identity:
        parts.append(f"=== IDENTITY ===\n{identity}")

    soul = _read_ws(WS_SOUL)
    if soul:
        parts.append(f"=== SOUL ===\n{soul}")

    rules = _read_ws(WS_RULES)
    if rules:
        parts.append(f"=== RULES ===\n{rules}")

    today_log = _read_ws(_today_log_path())
    if today_log:
        # Only include last 20 lines to stay within token budget
        lines = today_log.strip().splitlines()
        if len(lines) > 20:
            lines = lines[-20:]
        parts.append(f"=== TODAY'S LOG (last {len(lines)} entries) ===\n" + "\n".join(lines))

    return "\n\n".join(parts)


def load_memory_for_agent(agent_id: str) -> str | None:
    """On-demand: load MEMORY.md only if agent_id appears in it."""
    memory = _read_ws(WS_MEMORY)
    if not memory:
        return None
    # Check if this agent is mentioned (username or DID fragment)
    if agent_id.lower() in memory.lower():
        return memory
    return None


def load_heartbeat() -> str:
    """On-demand: load HEARTBEAT.md for heartbeat checks only."""
    return _read_ws(WS_HEARTBEAT)


# ---------------------------------------------------------------------------
# Workspace Memory — Rate Limit & Dedup
# ---------------------------------------------------------------------------

_MEMORY_ENTRY_RE = re.compile(
    r"^### (.+?) — (\d{4}-\d{2}-\d{2}) — .+$", re.MULTILINE
)
_MEMORY_REPLY_RE = re.compile(
    r"^→ Reply: (.+)$", re.MULTILINE
)


def _parse_memory_entries(agent_id: str) -> list[dict]:
    """Parse MEMORY.md and return entries for a specific agent."""
    memory = _read_ws(WS_MEMORY)
    if not memory:
        return []

    entries = []
    blocks = re.split(r"(?=^### )", memory, flags=re.MULTILINE)
    for block in blocks:
        header = _MEMORY_ENTRY_RE.search(block)
        if not header:
            continue
        name = header.group(1).strip()
        if name.lower() != agent_id.lower():
            continue
        date_str = header.group(2)
        reply_match = _MEMORY_REPLY_RE.search(block)
        reply_fp = reply_match.group(1).strip() if reply_match else ""
        entries.append({"name": name, "date": date_str, "reply_fp": reply_fp})

    return entries


def check_agent_rate_limit(agent_id: str) -> bool:
    """Return True if agent has hit the 24h reply rate limit."""
    entries = _parse_memory_entries(agent_id)
    if not entries:
        return False

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%d")
    recent = [e for e in entries if e["date"] >= cutoff]
    return len(recent) >= AGENT_REPLY_LIMIT_24H


def check_reply_dedup(agent_id: str, reply_text: str) -> str | None:
    """Check if first 5 words of reply match recent replies to this agent.
    Returns the matching fingerprint if duplicate found, None otherwise."""
    fingerprint = " ".join(reply_text.split()[:5])
    entries = _parse_memory_entries(agent_id)
    # Check last 10 entries
    for entry in entries[-10:]:
        if entry["reply_fp"] and entry["reply_fp"].lower() == fingerprint.lower():
            return fingerprint
    return None


# ---------------------------------------------------------------------------
# Workspace Memory Writer
# ---------------------------------------------------------------------------


def write_memory_entry(agent_id: str, date_str: str, context: str, status: str, reply_fingerprint: str = ""):
    """Append an entry to MEMORY.md after each reply."""
    entry = f"\n### {agent_id} — {date_str} — {context}\n→ Status: {status}\n"
    if reply_fingerprint:
        entry += f"→ Reply: {reply_fingerprint}\n"
    WS_MEMORY.parent.mkdir(parents=True, exist_ok=True)
    with open(WS_MEMORY, "a") as f:
        f.write(entry)
    log.info(f"MEMORY: wrote entry for {agent_id} ({status})")


# ---------------------------------------------------------------------------
# Workspace Log Writer
# ---------------------------------------------------------------------------


def write_log_entry(entry_type: str, message: str):
    """Append a timestamped entry to today's workspace log."""
    WS_LOGS.mkdir(parents=True, exist_ok=True)
    log_path = _today_log_path()
    now = datetime.now(timezone.utc).strftime("%H:%M")
    line = f"[{now}] {entry_type}: {message}\n"

    # Create daily log with header if new
    if not log_path.exists():
        header = f"# Ambassador Log — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
        log_path.write_text(header)

    with open(log_path, "a") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "seen_comments": {},    # post_id -> [comment_id, ...]
    "replies_posted": 0,    # total replies posted
    "agent_replies": {},    # author_name -> count of replies we sent them
    "nudged_agents": [],    # agents who already received a Stage 2 CTA
}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Math challenge solver (from heartbeat.py)
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
    ("plus", "+"), ("and", "+"), ("add", "+"), ("added", "+"), ("adding", "+"), ("adds", "+"),
    ("additional", "+"), ("total", "+"), ("combined", "+"), ("combine", "+"),
    ("minus", "-"), ("subtract", "-"), ("subtracted", "-"),
    ("less", "-"), ("reduced", "-"), ("reduces", "-"),
    ("decreased", "-"), ("decreases", "-"), ("decrease", "-"),
    ("slows", "-"), ("slowed", "-"),
    ("loses", "-"), ("lose", "-"), ("losing", "-"), ("lost", "-"),
    ("remaining", "-"), ("remains", "-"),
    ("times", "*"), ("multiplied", "*"), ("multiply", "*"),
    ("doubles", "*"), ("double", "*"), ("triples", "*"), ("triple", "*"),
    ("divided", "/"), ("divides", "/"), ("over", "/"),
    ("half", "/"), ("halves", "/"), ("halved", "/"),
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


def _legacy_regex_solve(text: str) -> str | None:
    clean = re.sub(r"[^a-zA-Z ]+", "", text).lower()
    words = [_collapse(w) for w in clean.split() if w]
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

    stream = _collapse(re.sub(r"[^a-zA-Z]", "", text).lower())
    num_entries = sorted(NUM_LOOKUP.items(), key=lambda x: (-len(x[0]), -x[1]))
    op_entries = sorted(OP_LOOKUP.items(), key=lambda x: len(x[0]), reverse=True)
    used: set[int] = set()
    stream_nums: list[tuple[int, int]] = []
    for word, val in num_entries:
        # Tolerant regex: allow 0-2 extra chars between target chars
        # Handles garbling like "thrirty" for "thirty"
        pat = "".join(re.escape(ch) + "[a-z]{0,2}" for ch in word)
        if pat.endswith("[a-z]{0,2}"):
            pat = pat[:-len("[a-z]{0,2}")]
        for m in re.finditer(pat, stream):
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

    # Handle implicit operands: "doubles" = x2, "triples" = x3, "half" = /2
    lowered = text.lower()
    nospace = re.sub(r"[^a-z]", "", lowered)
    if len(s_nums) == 1 or (len(s_nums) >= 2 and s_nums[0] == s_nums[1]):
        if "double" in nospace or "doubles" in nospace:
            return _compute(s_nums[0], 2.0, "*")
        if "triple" in nospace or "triples" in nospace:
            return _compute(s_nums[0], 3.0, "*")
        if "half" in nospace or "halve" in nospace:
            return _compute(s_nums[0], 2.0, "/")

    if len(s_nums) >= 2:
        return _compute(s_nums[0], s_nums[1], s_op)

    digits = [float(d) for d in re.findall(r"\d+\.?\d*", text)]
    if len(digits) >= 2:
        return _compute(digits[0], digits[1], op or "*")
    return None


# ---------------------------------------------------------------------------
# Moltbook API
# ---------------------------------------------------------------------------


def moltbook_get(client: httpx.Client, path: str, **params) -> dict | list | None:
    try:
        r = client.get(
            f"{MOLTBOOK_BASE}{path}",
            headers={"Authorization": f"Bearer {MOLTBOOK_KEY}"},
            params=params,
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        log.warning(f"GET {path} -> {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"GET {path} error: {e}")
    return None


def moltbook_post(client: httpx.Client, path: str, body: dict) -> dict | None:
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
                retry_after = r.json().get("retry_after_seconds", 25)
                log.info(f"Rate limited, waiting {retry_after}s (attempt {attempt+1}/3)")
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
    result = moltbook_post(client, "/verify", {"verification_code": code, "answer": answer})
    if result and result.get("success"):
        log.info("Verification solved!")
        return True
    log.error(f"Verification failed: {result}")
    return False


def get_our_posts(client: httpx.Client) -> list[dict]:
    data = moltbook_get(client, "/posts", author=OUR_AUTHOR, limit=50)
    if not data:
        return []
    return data if isinstance(data, list) else data.get("posts", data.get("data", []))


# How far back a thread may have gone quiet and still be read, and a hard
# ceiling on how many threads one run opens. Measured 2026-10-01: a comment
# listing costs about 0.25 s, so eighty threads add roughly twenty seconds to a
# run that otherwise takes under ten. The age window is what actually sheds dead
# threads; the count is a backstop for the day the account comments far more.
THREAD_LOOKBACK_DAYS = 14
MAX_THREADS_PER_RUN = 80


def threads_we_are_in(client: httpx.Client) -> list[dict]:
    """Posts where this account already has a comment, newest activity first.

    Reading only our own posts starved the agent: on 2026-10-01 the
    author-filtered listing returned exactly one post, and between midnight and
    09:00 UTC it returned none at all, so nineteen of thirty-three runs ended
    before they looked at a single comment. A thread we have already spoken in is
    not an uninvited appearance, and it is where the replies to us arrive.

    The post title and body are not fetched here. They are only needed once a
    comment has survived every filter, and fetching them for eighty threads a
    run would double the request count to no purpose.
    """
    data = moltbook_get(client, "/agents/me/comments", limit=100)
    rows = (data or {}).get("comments") if isinstance(data, dict) else data
    if not rows:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=THREAD_LOOKBACK_DAYS)
    newest: dict[str, datetime] = {}
    for c in rows:
        pid = c.get("post_id")
        when = _parse_stamp(c.get("created_at"))
        if not pid or when is None:
            continue
        if when < cutoff:
            continue
        if pid not in newest or when > newest[pid]:
            newest[pid] = when

    ordered = sorted(newest.items(), key=lambda kv: kv[1], reverse=True)
    return [{"id": pid, "comment_count": None, "source": "commented"}
            for pid, _ in ordered[:MAX_THREADS_PER_RUN]]


def _parse_stamp(stamp: str | None) -> datetime | None:
    try:
        when = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def read_surface(client: httpx.Client) -> list[dict]:
    """Every thread this run looks at: our own posts first, then ours-by-comment.

    Own posts keep their place at the front and keep their metadata, so nothing
    about how they are handled changes. A thread reachable both ways appears
    once, as an own post.
    """
    surface = list(get_our_posts(client))
    have = {p["id"] for p in surface}
    for p in surface:
        p.setdefault("source", "own")
    for t in threads_we_are_in(client):
        if t["id"] not in have:
            surface.append(t)
            have.add(t["id"])
    return surface


def get_post(client: httpx.Client, post_id: str) -> dict:
    """One post by id, for threads that did not arrive with their metadata."""
    data = moltbook_get(client, f"/posts/{post_id}")
    if not isinstance(data, dict):
        return {}
    inner = data.get("post")
    return inner if isinstance(inner, dict) else data


def get_comments(client: httpx.Client, post_id: str) -> list[dict]:
    data = moltbook_get(client, f"/posts/{post_id}/comments")
    if not data:
        return []
    return data.get("comments", [])


class ReplyWithheld(Exception):
    """The content rule stopped a draft before it reached the network.

    Distinct from a returned `None`, which means the network refused the reply.
    A refusal settles the comment; a withheld draft does not. The caller has to
    tell them apart, or one draft tripping the word filter buries the question
    behind it for good.
    """


def post_reply(client: httpx.Client, post_id: str, content: str, parent_id: str) -> dict | None:
    # Last gate before the network. The reply text comes from a model, so the
    # stage instruction is guidance and this is the rule: a draft that carries
    # a link, an install line or a credit offer does not go out, whatever
    # stage asked for it.
    broken = content_violations("", content)
    if broken:
        log.error(f"reply withheld ({', '.join(broken)}): {content[:80]}")
        raise ReplyWithheld(", ".join(broken))
    body = {"content": content, "parent_id": parent_id}
    result = moltbook_post(client, f"/posts/{post_id}/comments", body)
    if result:
        solve_verification(client, result)
    return result


# ---------------------------------------------------------------------------
# Claude reply generation (with workspace bootstrap)
# ---------------------------------------------------------------------------

# Low-effort patterns to skip
LOW_EFFORT_PATTERNS = re.compile(
    r"^(\+1|nice|cool|great|thanks|lol|wow|ok|yes|no|agreed|this|same|love it|fire|based|true|real|💯|🔥|👍|❤️|🙌|👏|💪|✅)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# The form every reply takes, whatever stage it is written at. The rules the
# gate enforces belong here too: a rule the model only meets after the draft is
# written costs a model call and a question. Between 27. and 28.09.2026 the gate
# refused 28 of 36 drafts, 15 of them on g1a alone, because the instruction said
# nothing about sentence shape.
REPLY_FORM = """=== FORM (all of this is binding) ===

Length: at most 120 words, in one or two paragraphs. Count them.

Plain text. No markdown at all: no heading line, no '#', no '*' or '_' around
words, no bullet or numbered list, no block quote, no backticks, no table, no
'---' rule between paragraphs. The comment field renders none of it, so a line
reading '# Reply to someone' posts as those literal characters.

=== WHAT YOU MAY ASSERT (checked as rules c1 to c3, and each one refuses the
whole draft) ===

c1. No score, rating, trust level or confidence figure for the agent you are
    answering, and no verdict on their standing. This process computes none of
    those and has no access to them. We sell agent trust scoring; a score we
    published without computing it would refute the product in the act of
    demonstrating it.

c2. No tool call and no tool output. You have no tools here. Nothing that reads
    'Checking agent trust score for...', 'Score: 67', 'Verified: yes',
    'Status: ...', 'Result: ...'. Your whole output is the comment itself:
    nothing before it, nothing after it, no preamble, no status line, no
    narration of a step, no separator line.

c3. Every figure you write has to come from the comment you are answering or
    from our own pages quoted to you. Do not estimate, do not illustrate with a
    round number, do not invent an example figure. With no figure available from
    those two places, write the sentence without one.

Do not open by judging what the other agent did or wrote. Not 'you've just
separated...', not 'this is the right decomposition', not 'you're naming
something most people miss', not 'good point', not 'fair challenge'. Open on the
subject itself: the case, the mechanism, the boundary, the consequence.

Sentence shapes that are refused, the first one above all others:

1. Antithesis used as a frame: 'not X, but Y', 'it isn't just A, it's B',
   'that's not P, that's Q', 'less A, more B'. An antithesis is allowed only
   where the contrast carries the claim itself, as in: "a track record only its
   issuer can compute is a reputation service; one any party can recompute is
   evidence." If removing the contrast would leave the sentence saying the same
   thing, remove it.
2. More than one contrast pair in a paragraph.
3. An em dash insert holding a list of restatements. One insert per sentence at
   most, carrying one thought.
4. Three short parallel sentences running into a question.
5. A verbless fragment tacked on for effect ("That's the wedge.").
6. A closing sentence that says the opening one again in other words.
7. Pseudo-cleft: 'what X does is Y', 'X is what lets Y'. Write 'X does Y'.
8. Two sentences built the same way, back to back.
9. Stacked superlatives, or a judgement with nothing behind it.
10. A rhetorical question as the opener.

End with a question only when it asks for one specific thing: a number, a
mechanism, a boundary, or a choice between two named options. A question that
invites someone to elaborate is not a question. With no such question, end on
the statement.

=== TARGET FORM ===

Write in this shape:

{target_form}

One thought, carried to a consequence. No markdown. No verdict on the other
agent. A closing question that asks for one named thing.

The example is there to show the shape. Its sentences are not yours: do not
reuse them, do not adapt them, do not answer a different comment with them. A
draft that comes back close to the example is thrown away whole."""
REPLY_FORM = REPLY_FORM.format(target_form=comment_gate.TARGET_FORM)


def forbidden_words_line() -> str:
    """The gate's own word list, for the instruction that precedes the gate.

    Read from the same anti-KI-Sprech.md the rule reads, so the two cannot drift
    apart. An empty lexicon yields an empty section rather than a stale copy.
    """
    try:
        words = voice_gate.load_rules(refresh=False).get("banned", [])
    except Exception as e:  # noqa: BLE001 - a prompt without the list beats no draft
        log.warning(f"banned lexicon unavailable for the prompt: {type(e).__name__}")
        return ""
    if not words:
        return ""
    return ("\n\n=== FORBIDDEN WORDS (any one of them fails the draft) ===\n"
            + ", ".join(sorted(words)))


STAGE_1_INSTRUCTION = """Answer the substance and sell nothing. Do not mention
registering, verifying, signing up or trying anything out. Do not name
moltrust.ch or any product, ours or anyone's.

Say one thing they can check: a failure case, a mechanism, a boundary, or a
figure that came from their own comment. If you have nothing checkable to add,
write the one thing you do know and stop there."""

STAGE_2_INSTRUCTION = """This agent has commented before and has asked for
something concrete. Answer what they asked, in full, first.

Then at most one plain sentence saying where the thing they asked about lives.
If the answer does not need that sentence, leave it out.

That sentence carries no imperative aimed at them, no offer, no price, no
install command, no link, no domain name, no mention of credits, and never the
word 'free'. Each of those is refused outright before the reply reaches the
network, and the refusal throws away the whole draft, answer included. The three
worked examples that used to stand here all broke that rule, so they are gone.
These three do not:

{examples}

Say something of that shape, or say nothing."""
STAGE_2_INSTRUCTION = STAGE_2_INSTRUCTION.format(
    examples="\n".join(f"  \"{e}\"" for e in comment_gate.STAGE_2_EXAMPLES))

STAGE_3_INSTRUCTION = """This is an agent you've spoken to several times, and
they have had a product suggestion already.

Answer the substance. Do not repeat a suggestion, a nudge or a call to action —
once was enough. If they ask about registration or verification outright, answer
that question plainly, under the same content rule as every other reply."""


# ---------------------------------------------------------------------------
# Thread context builder (compaction for long threads)
# ---------------------------------------------------------------------------


def build_thread_context(all_comments: list, current_comment_id: str, post_title: str) -> str:
    """Build thread history for Claude. Max 10 comments, with summary for long threads."""
    preceding = []
    for c in all_comments:
        if c["id"] == current_comment_id:
            break
        author = c.get("author", {}).get("name", "unknown")
        content = c.get("content", "")
        if content.strip():
            preceding.append({"author": author, "content": content})

    if not preceding:
        return ""

    total = len(preceding)

    if total <= 10:
        lines = [f"[{c['author']}]: {c['content']}" for c in preceding]
        return "Thread history:\n" + "\n".join(lines)

    last_10 = preceding[-10:]
    lines = [f"[{c['author']}]: {c['content']}" for c in last_10]

    if total > 20:
        summary = summarize_thread(preceding[:-10], post_title)
        return (
            f"Thread summary ({total} comments total, showing last 10):\n"
            f"{summary}\n\n"
            f"Recent comments:\n" + "\n".join(lines)
        )

    return (
        f"Thread history (showing last 10 of {total} comments):\n" + "\n".join(lines)
    )


def summarize_thread(older_comments: list, post_title: str) -> str:
    """Generate a brief summary of older thread comments via Claude."""
    text = "\n".join(
        f"[{c['author']}]: {c['content'][:120]}"
        for c in older_comments[-15:]
    )
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
                "max_tokens": 100,
                "messages": [{"role": "user", "content": (
                    f"Summarize this thread discussion in 1-2 sentences. "
                    f"Post title: '{post_title}'\n\n{text}"
                )}],
            },
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"].strip()
    except Exception:
        pass

    authors = set(c["author"] for c in older_comments)
    return f"Discussion between {', '.join(list(authors)[:5])} about '{post_title}'"


def generate_reply(
    post_title: str,
    post_content: str,
    comment_author: str,
    comment_text: str,
    stage: int,
    thread_context: str = "",
    session_id: str = "",
    avoid_opening: str = "",
    redraft_note: str = "",
) -> str | None:
    """Generate a reply using Claude with workspace bootstrap context."""
    if stage == 1:
        stage_instruction = STAGE_1_INSTRUCTION
    elif stage == 2:
        stage_instruction = STAGE_2_INSTRUCTION
    else:
        stage_instruction = STAGE_3_INSTRUCTION

    # --- Bootstrap: load workspace identity, soul, rules ---
    bootstrap = load_bootstrap()

    # --- On-demand: load memory if this agent is known ---
    agent_memory = load_memory_for_agent(comment_author)
    memory_section = ""
    if agent_memory:
        memory_section = f"\n\n=== MEMORY (prior interactions with {comment_author}) ===\n{agent_memory}"

    # --- Dedup instruction if retrying ---
    dedup_section = ""
    if avoid_opening:
        dedup_section = (
            f"\n\nIMPORTANT: Your previous reply started with '{avoid_opening}'. "
            "Do NOT repeat this framing. Use a completely different angle, "
            "different opening sentence, different structure."
        )

    # --- What the gate said about the first draft, if there was one ---
    # Composed by comment_gate from the same lists the rules read, so the model
    # is told what it tripped rather than being asked to guess.
    words_section = f"\n\nIMPORTANT: {redraft_note}" if redraft_note else ""

    # Build system prompt from workspace files + stage instruction. The form
    # block and the word list come last, so nothing in the workspace files can
    # read as an exception to them.
    system = (bootstrap + memory_section + "\n\n=== STAGE INSTRUCTION ===\n"
              + stage_instruction + dedup_section + words_section
              + "\n\n" + REPLY_FORM + forbidden_words_line())

    # Build user message with thread context
    parts = [f"Post title: {post_title}", f"Post content: {post_content[:500]}"]
    if thread_context:
        parts.append(f"\n{thread_context}")
    parts.append(f"\nComment by {comment_author} (reply to this one):\n{comment_text}")
    parts.append("\nWrite a reply to this comment. You have full thread context above — reference earlier points if relevant.")
    if session_id:
        parts.append(f"\n[session: {session_id}]")
    user_msg = "\n".join(parts)

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
                "max_tokens": 500,
                "system": system,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            return texts[0].strip() if texts else None
        log.warning(f"Claude API -> {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"Claude API error: {e}")
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def get_stage(state: dict, author_name: str, comment_text: str = "") -> int:
    """Which reply stage this comment earns.

    Stage 1 is substantive and says nothing about the product. Stages 2 and 3
    carry an offer, and an offer is earned by being asked for — not by having
    replied to somebody twice before.

    The old rule escalated on a counter: first reply substantive, second a
    nudge, third and onward the full pitch, whatever the person had said. On
    2026-09-23 that produced a stage-3 reply to somebody discussing delegation
    scope. It is also the likeliest reason Moltbook marks 91 of this account's
    last hundred comments as spam while the posts carry no mark at all.
    """
    if not asked_for_an_offer(comment_text):
        return 1
    prior = state.get("agent_replies", {}).get(author_name, 0)
    nudged = author_name in state.get("nudged_agents", [])
    if prior == 0 or not nudged:
        return 2
    return 3


def record_reply(state: dict, author_name: str, stage: int):
    """Update state after posting a reply."""
    if "agent_replies" not in state:
        state["agent_replies"] = {}
    state["agent_replies"][author_name] = state["agent_replies"].get(author_name, 0) + 1
    if stage == 2:
        if "nudged_agents" not in state:
            state["nudged_agents"] = []
        if author_name not in state["nudged_agents"]:
            state["nudged_agents"].append(author_name)


def _stage_to_status(stage: int) -> str:
    """Map CTA stage to memory status string."""
    return {1: "first_contact", 2: "second_contact", 3: "verified"}.get(stage, "first_contact")


def cmd_run(state: dict):
    """Check for new comments and auto-reply, inside the comment gate."""
    log.info("=== RUN: checking for new comments ===")

    comment_gate.arm(state)
    room, why, reading = comment_gate.run_allowance(state, MOLTBOOK_KEY)
    log.info(f"Comment gate: {why}")
    if room == 0:
        # A blocked run is the normal outcome of a bad rate, so it is reported
        # once a day rather than every thirty minutes.
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if reading.get("mode") in ("blocked", "unreadable") and \
                state.get("gate_reported_on") != stamp:
            state["gate_reported_on"] = stamp
            notify.send_telegram(
                f"\U0001f6d1 Moltbook-Kommentare gesperrt\n\n{why}\n\n"
                f"Der Ambassador schreibt nichts, bis die Quote unter "
                f"{comment_gate.SPAM_BLOCK_PCT} % liegt.", channel=notify.ALERTS)
        # No save here: `main` writes the state once, after this returns. The
        # extra write made `cmd_run` the only code path that touched
        # ~/.ambassador_state.json without going through `main`, so a pytest run
        # calling it directly overwrote the live agent's answered-comment ids —
        # on 27.09.2026 and again on 28.09.2026.
        return

    # Our own published pages, so a figure taken from them can be shown to come
    # from somewhere. Rule (h) blocks a claim that appears in no cited source,
    # and a comment citing nothing is exactly what got 128 of them marked spam.
    kb = {}
    try:
        kb = reply_radar.load_kb()
    except Exception as e:
        log.warning(f"KB unavailable, gate (h) will block every claim: {type(e).__name__}")

    # What we have already said, whoever we said it to, newest first. c8 reads
    # this; without it the rule cannot fire and a repeated opening goes out, as
    # one did on 2026-10-01. The list grows as this run posts, so two replies in
    # the same run cannot open the same way either — the case the old per-sender
    # fingerprint was blindest to, because the two senders differ.
    recent_ours: list[str] = []
    _rows = comment_gate._our_comments(MOLTBOOK_KEY)
    if _rows is None:
        log.warning("Our own comment list is unreadable; c8 cannot compare "
                    "against it this run")
    else:
        recent_ours = [c.get("content") or ""
                       for c in _rows[:comment_gate.DEDUP_WINDOW]]
        log.info(f"c8 compares against our last {len(recent_ours)} comments")

    with httpx.Client() as client:
        posts = read_surface(client)
        if not posts:
            log.info("No posts found")
            return

        own = sum(1 for p in posts if p.get("source") == "own")
        log.info(f"Found {len(posts)} posts: {own} our own, "
                 f"{len(posts) - own} threads we have commented in")
        # One reply per sender per run. The 24-hour limit allows three, which is
        # right across a day and wrong inside a single pass: on 2026-09-28 two
        # drafts went to the same account in one run, answering two comments
        # that were both echoes of our own post title. A thread reads worse for
        # the second one than it reads better.
        answered_this_run: set[str] = set()
        # The per-run allowance was never enforced inside the loop. `room` was
        # decremented on a successful post and never read again, so a run wrote
        # as many comments as it found candidates. With one own post and three
        # comments that was invisible. With the thread surface widened to every
        # post this account has commented in, one run would face hundreds of
        # candidates and spend the whole day's allowance, and the model calls
        # with it, in a single pass.
        out_of_room = False
        replied = 0
        skipped_low_effort = 0
        skipped_rate_limit = 0
        skipped_off_topic = 0
        skipped_gate = 0

        for post in posts:
            if out_of_room:
                break
            post_id = post["id"]
            title = post.get("title", "(untitled)")
            content = post.get("content", "")
            comment_count = post.get("comment_count", 0)

            if comment_count == 0:
                continue

            comments = get_comments(client, post_id)
            seen = set(state["seen_comments"].get(post_id, []))

            # Collect all comments including nested replies
            all_comments = []
            def _collect(clist):
                for c in clist:
                    all_comments.append(c)
                    if isinstance(c.get("replies"), list):
                        _collect(c["replies"])
            _collect(comments)

            for comment in all_comments:
                if room <= 0:
                    # Stop here rather than draft for a reply that may not go
                    # out. What is left stays unseen and is looked at next run.
                    out_of_room = True
                    break
                cid = comment["id"]
                if cid in seen:
                    continue

                author_name = comment.get("author", {}).get("name", "unknown")

                # Skip anything written by us — either account, not just the
                # one this process runs as. OUR_AUTHOR alone let moltguard_v1's
                # comments through as though they were a stranger's, so a duo
                # comment from our own sister account could draw a reply and
                # the two would hold a conversation on our own post.
                if is_our_account(author_name, comment.get("author_id", "")):
                    seen.add(cid)
                    continue

                comment_text = comment.get("content", "")
                if not comment_text.strip():
                    seen.add(cid)
                    continue

                # Skip low-effort comments
                if LOW_EFFORT_PATTERNS.match(comment_text.strip()):
                    log.info(f"Skipping low-effort comment by {author_name}: {comment_text[:40]}")
                    seen.add(cid)
                    skipped_low_effort += 1
                    continue

                # Relevance, before a single token is spent on a reply.
                on_topic, reason = comment_gate.worth_answering(comment_text)
                if not on_topic:
                    log.info(f"Skipping {author_name}: {reason}")
                    write_log_entry("SKIP", f"{author_name}: {reason}")
                    seen.add(cid)
                    skipped_off_topic += 1
                    continue

                if author_name in answered_this_run:
                    log.info(f"Skipping {author_name}: i5 Zweite Antwort im "
                             f"selben Lauf")
                    write_log_entry("SKIP", f"{author_name}: i5 second reply in "
                                            f"the same run")
                    skipped_rate_limit += 1
                    continue

                # --- Fix 2a: Rate limit per agent (3 replies / 24h) ---
                if check_agent_rate_limit(author_name):
                    log.info(f"SKIP {author_name}: rate limit ({AGENT_REPLY_LIMIT_24H}/24h reached)")
                    write_log_entry("SKIP", f"{author_name}: rate limit ({AGENT_REPLY_LIMIT_24H}/24h reached)")
                    seen.add(cid)
                    skipped_rate_limit += 1
                    continue

                # --- Session isolation: unique session per interaction ---
                session_id = f"ambassador_{post_id}_{cid}"

                # A thread from our own comment list carries no title and no
                # body, because the comment listing does not include them. Fetch
                # them here, once per thread, now that a comment has survived
                # every filter: a draft written against "(untitled)" and an empty
                # post answers the comment without knowing what it is about.
                if not post.get("title"):
                    fetched = get_post(client, post_id)
                    post["title"] = fetched.get("title") or "(untitled)"
                    post["content"] = fetched.get("content") or ""
                    title, content = post["title"], post["content"]
                    if title == "(untitled)":
                        log.warning(f"post {post_id[:8]} carries no title; "
                                    f"drafting against the comment alone")

                # Determine stage
                stage = get_stage(state, author_name, comment_text)
                log.info(f"New comment by {author_name} (stage {stage}) on '{title[:40]}': {comment_text[:80]}...")

                # Build thread context
                thread_context = build_thread_context(all_comments, cid, title)
                if thread_context:
                    log.info(f"Thread context: {len(thread_context)} chars")

                # Generate reply (with bootstrap + session isolation)
                reply_text = generate_reply(
                    title, content, author_name, comment_text, stage,
                    thread_context=thread_context,
                    session_id=session_id,
                )
                if not reply_text:
                    log.warning(f"Failed to generate reply for comment {cid[:8]}")
                    seen.add(cid)
                    continue

                # --- Fix 2b: Dedup via fingerprint ---
                dup_fp = check_reply_dedup(author_name, reply_text)
                if dup_fp:
                    log.info(f"Dedup: reply to {author_name} starts with '{dup_fp}' (seen before), regenerating...")
                    reply_text = generate_reply(
                        title, content, author_name, comment_text, stage,
                        thread_context=thread_context,
                        session_id=session_id,
                        avoid_opening=dup_fp,
                    )
                    if not reply_text:
                        log.warning(f"Dedup regeneration failed for {author_name}, skipping")
                        seen.add(cid)
                        continue
                    # Check again — if still duplicate, skip entirely
                    dup_fp2 = check_reply_dedup(author_name, reply_text)
                    if dup_fp2:
                        log.info(f"Dedup: still duplicate after retry for {author_name}, skipping")
                        write_log_entry("SKIP", f"{author_name}: dedup failed after retry")
                        seen.add(cid)
                        continue

                # Where the question is about identity, the reply carries the
                # agent's own attestation. A claim about being checkable that
                # arrives with nothing to check is what the old comment pools
                # sent a thousand times.
                if is_identity_question(comment_text):
                    token = gate_attestation()
                    if token:
                        reply_text += ("\n\nMine, if you would rather check than take my word: "
                                       + token)
                    else:
                        log.info("identity question, but no attestation available — replying without it")

                # Both gates over what we are about to send. A comment that
                # carries no figure, or one that appears in none of our own
                # pages, does not go out — the rule the reply radar already
                # runs under, for the same reason.
                require_number = comment_gate.needs_number(comment_text)
                passed, problems = comment_gate.check_reply(
                    reply_text, kb, require_number=require_number,
                    comment_text=comment_text,
                    recent_comments=recent_ours)

                # A banned word and an overlong draft are faults in the wording,
                # not in the answer. Naming them and drafting once more costs one
                # model call; losing the question costs the question. Only the
                # second draft's verdict counts. Nothing else earns a retry — a
                # draft that invents a score is not one redraft from being right.
                if not passed:
                    note = comment_gate.redraft_note(reply_text, recent_ours)
                    if note:
                        log.info(f"Redrafting the reply to {author_name}: {note[:110]}")
                        second = generate_reply(
                            title, content, author_name, comment_text, stage,
                            thread_context=thread_context,
                            session_id=session_id,
                            redraft_note=note,
                        )
                        if second:
                            reply_text = second
                            passed, problems = comment_gate.check_reply(
                                reply_text, kb, require_number=require_number,
                                comment_text=comment_text,
                                recent_comments=recent_ours)
                            if not passed and comment_gate.must_discard(problems):
                                # Asked once to open differently, came back with
                                # the same opening. A third ask gets it again.
                                log.info(f"Discarding the reply to {author_name}: "
                                         f"the redraft repeats it too — "
                                         f"{'; '.join(problems)[:140]}")
                                write_log_entry("SKIP", f"{author_name}: c8 — "
                                                        "discarded after one redraft")
                                comment_gate.clear_attempt(state, cid)
                                seen.add(cid)
                                skipped_gate += 1
                                continue
                        else:
                            log.warning(f"Redraft failed for {author_name}")

                if not passed:
                    # The comment stays open, the way a withheld draft does
                    # (#487): the gate refusing a draft settles nothing about
                    # the question. A ceiling keeps that from drafting forever.
                    tries = comment_gate.note_attempt(state, cid)
                    left = comment_gate.attempts_left(state, cid)
                    reason = '; '.join(problems)[:160]
                    if left:
                        log.info(f"Gate blocked the reply to {author_name} "
                                 f"(attempt {tries}/{comment_gate.GATE_MAX_ATTEMPTS}, "
                                 f"open for the next run): {reason}")
                        write_log_entry("SKIP", f"{author_name}: gate attempt "
                                                f"{tries}/{comment_gate.GATE_MAX_ATTEMPTS} — "
                                                f"{'; '.join(problems)[:120]}")
                    else:
                        log.info(f"Gate blocked the reply to {author_name} "
                                 f"{tries} times, giving up on comment {cid[:8]}: "
                                 f"{reason}")
                        write_log_entry("SKIP", f"{author_name}: gate — gave up after "
                                                f"{tries} attempts: "
                                                f"{'; '.join(problems)[:100]}")
                        comment_gate.clear_attempt(state, cid)
                        seen.add(cid)
                    skipped_gate += 1
                    continue

                comment_gate.clear_attempt(state, cid)

                log.info(f"Reply (stage {stage}, session {session_id}): {reply_text[:100]}...")

                # Post reply directly
                try:
                    result = post_reply(client, post_id, reply_text, cid)
                except ReplyWithheld as exc:
                    # The draft is gone, the question is not. Leaving `cid`
                    # unseen puts it back in front of the next run, which drafts
                    # again from the same comment. Marking it here is what kept
                    # two legitimate questions unanswered on 24. and 25.09.2026,
                    # both because a draft happened to carry the word "free".
                    log.warning(
                        f"comment {cid[:8]} stays unseen after a withheld draft "
                        f"({exc}) — the next run drafts again"
                    )
                    time.sleep(2)
                    continue
                if result:
                    replied += 1
                    room -= 1
                    answered_this_run.add(author_name)
                    # Newest first, so the comment just sent is the first thing
                    # the next draft in this run is compared against.
                    recent_ours.insert(0, reply_text)
                    comment_gate.count_comment(state)
                    state["replies_posted"] = state.get("replies_posted", 0) + 1
                    record_reply(state, author_name, stage)
                    log.info(f"Posted stage-{stage} reply to {author_name} on '{title[:40]}'")

                    # --- Memory writer: record interaction with fingerprint ---
                    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    context_short = title[:60] if len(title) <= 60 else title[:57] + "..."
                    reply_fp = " ".join(reply_text.split()[:5])
                    write_memory_entry(
                        agent_id=author_name,
                        date_str=date_str,
                        context=context_short,
                        status=_stage_to_status(stage),
                        reply_fingerprint=reply_fp,
                    )

                    # --- Log writer: record reply ---
                    write_log_entry("REPLY", f"to {author_name}: {reply_text[:80]}")
                else:
                    log.warning(f"Failed to post reply for comment {cid[:8]}")

                seen.add(cid)
                time.sleep(2)  # pace ourselves between replies

            state["seen_comments"][post_id] = list(seen)

    # --- Log writer: run summary ---
    # The gate and the relevance filter are where the runs of 26.–28.09.2026
    # ended, and neither appeared in this line, so a day of no replies read the
    # same whether nothing arrived or everything was refused.
    if out_of_room:
        log.info("Run allowance spent; the remaining comments stay open for the "
                 "next run")
    summary = (f"{replied} replies, {skipped_rate_limit} rate-limited, "
               f"{skipped_low_effort} low-effort, {skipped_off_topic} off-topic, "
               f"{skipped_gate} gate")
    write_log_entry("HEARTBEAT", summary)
    log.info(f"Run done: {summary}")



# ---------------------------------------------------------------------------
# Daily post generation for m/agenttrust
# ---------------------------------------------------------------------------

AGENTTRUST_SUBMOLT = "agenttrust"

POST_TOPICS = [
    "agent identity standards and interoperability",
    "reputation systems for autonomous agents",
    "Sybil resistance in agent networks",
    "verifiable credentials for AI agents",
    "on-chain vs off-chain agent identity",
    "trust in multi-agent collaboration",
    "agent accountability and auditability",
    "privacy-preserving identity verification",
    "cross-platform agent reputation portability",
    "integrity monitoring in agent marketplaces",
    "decentralized identity for agent commerce",
    "behavioral vs cryptographic trust signals",
    "agent trust in prediction markets",
    "zero-knowledge proofs for agent identity",
    "the role of DIDs in the agent economy",
    "ERC-8004 and on-chain agent registries",
    "trust frameworks for agent-to-agent payments",
    "governance in agent networks",
    "credential revocation for misbehaving agents",
    "human oversight vs agent autonomy in trust decisions",
]

POST_SYSTEM_PROMPT = """You are the MolTrust Ambassador posting discussion topics in m/agenttrust on Moltbook.
m/agenttrust is a submolt (community) focused on agent identity, trust, and reputation.

Your posts should:
- Be thoughtful, technical discussion starters about agent trust topics
- Present multiple perspectives and ask open questions
- Include concrete examples, numbers, or references where possible
- Be 200-400 words long
- NOT be promotional for MolTrust — focus purely on the topic
- NOT mention moltrust.ch, pip install, or any product pitch
- End with 1-2 discussion questions to drive engagement
- Use markdown formatting (bold, lists, etc.)

Tone: Knowledgeable peer, not a marketer. Think "interesting blog post" not "product announcement"."""


def generate_post_content(topic: str, previous_titles: list[str]) -> tuple[str, str] | None:
    """Generate a title and body for a new m/agenttrust post."""
    prev_list = "\n".join(f"- {t}" for t in previous_titles[-10:]) if previous_titles else "None yet"

    user_msg = (
        f"Generate a discussion post about: {topic}\n\n"
        f"Previous post titles (do NOT repeat these topics):\n{prev_list}\n\n"
        f"Return your response in this exact format:\n"
        f"TITLE: Your Post Title Here\n"
        f"BODY:\nYour post body here..."
    )
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
                "system": POST_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            if not texts:
                return None
            text = texts[0].strip()
            title_match = re.search(r"TITLE:\s*(.+?)\n", text)
            body_match = re.search(r"BODY:\s*\n(.+)", text, re.DOTALL)
            if title_match and body_match:
                return title_match.group(1).strip(), body_match.group(1).strip()
            lines = text.split("\n", 1)
            if len(lines) == 2:
                return lines[0].strip().lstrip("# "), lines[1].strip()
        log.warning(f"Claude API -> {r.status_code}")
    except Exception as e:
        log.error(f"Claude API error: {e}")
    return None


def cmd_post(state: dict):
    """Generate and post a new discussion topic to m/agenttrust."""
    log.info("=== POST: generating new m/agenttrust topic ===")

    posted_topics = state.get("agenttrust_posts", [])
    posted_titles = [p.get("title", "") for p in posted_topics]

    topic_index = len(posted_topics) % len(POST_TOPICS)
    topic = POST_TOPICS[topic_index]
    log.info(f"Topic #{topic_index}: {topic}")

    result = generate_post_content(topic, posted_titles)
    if not result:
        log.error("Failed to generate post content")
        return
    title, body = result
    log.info(f"Generated: {title}")

    with httpx.Client() as client:
        post_data = moltbook_post(client, "/posts", {
            "title": title,
            "content": body,
            "submolt_name": AGENTTRUST_SUBMOLT,
        })
        if not post_data:
            log.error("Failed to post to Moltbook")
            return

        solve_verification(client, post_data)

        post_id = post_data.get("post", {}).get("id", "unknown")
        log.info(f"Posted to m/agenttrust: {post_id}")
        mark_active(AMBASSADOR_DID)  # FIX 1

    if "agenttrust_posts" not in state:
        state["agenttrust_posts"] = []
    state["agenttrust_posts"].append({
        "title": title,
        "post_id": post_id,
        "topic": topic,
        "date": datetime.now(timezone.utc).isoformat(),
    })
    log.info(f"Total m/agenttrust posts: {len(state['agenttrust_posts'])}")

    # --- Log writer ---
    write_log_entry("POST", f"m/agenttrust: {title[:80]}")


def cmd_status(state: dict):
    seen_total = sum(len(v) for v in state.get("seen_comments", {}).values())
    posts_tracked = len(state.get("seen_comments", {}))
    total_replies = state.get("replies_posted", 0)

    # Show workspace bootstrap status
    ws_files = ["IDENTITY.md", "SOUL.md", "RULES.md", "MEMORY.md", "HEARTBEAT.md", "TOOLS.md"]
    ws_status = []
    for f in ws_files:
        p = WORKSPACE / f
        if p.exists():
            size = p.stat().st_size
            ws_status.append(f"  {f}: {size} bytes")
        else:
            ws_status.append(f"  {f}: MISSING")

    log.info(f"Status: {posts_tracked} posts tracked, {seen_total} comments seen, {total_replies} replies posted")
    log.info(f"Workspace ({WORKSPACE}):")
    for s in ws_status:
        log.info(s)

    today_log = _today_log_path()
    if today_log.exists():
        lines = today_log.read_text().strip().splitlines()
        log.info(f"Today's log: {len(lines)} lines")
    else:
        log.info("Today's log: not yet created")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------



AMBASSADOR_DUO_PERSONA = (
    "You are u/moltrust-agent, MolTrust's Moltbook voice: curious, opinionated, "
    "lightly ironic, anti-marketing. Your duo partner is u/moltguard_v1, the "
    "security watchdog - you ask the trust-design question, he brings the data."
)


def _cmd_duo():
    """One rate-limited cross-comment onto a recent u/moltguard_v1 post."""
    from lib.moltbook_duo import run_duo
    run_duo("moltrust-agent", "moltguard_v1", MOLTBOOK_KEY, ANTHROPIC_KEY,
            AMBASSADOR_DUO_PERSONA, WORKSPACE / "duo_state.json", log)


def main():
    parser = argparse.ArgumentParser(description="MolTrust Ambassador Agent")
    parser.add_argument("command", choices=["run", "status", "post", "duo"],
                        help="run=check comments & auto-reply, status=print stats, post=new m/agenttrust topic")
    args = parser.parse_args()

    init_keys()

    missing = []
    if not MOLTBOOK_KEY:
        missing.append("MOLTBOOK_AGENT_KEY")
    if not ANTHROPIC_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        log.error(f"Missing keys: {', '.join(missing)}")
        return

    now = datetime.now(timezone.utc)
    log.info(f"\n{'='*50}")
    log.info(f"MOLTRUST AMBASSADOR — {args.command}")
    log.info(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Workspace: {WORKSPACE}")
    log.info(f"{'='*50}")

    state = load_state()

    if args.command == "run":
        cmd_run(state)
    elif args.command == "status":
        cmd_status(state)
    elif args.command == "post":
        cmd_post(state)
    elif args.command == "duo":
        _cmd_duo()

    save_state(state)
    log.info("Done.\n")


if __name__ == "__main__":
    main()
