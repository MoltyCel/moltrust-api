"""What has to be true before the ambassador comments on Moltbook.

Three gates, in the order they get cheaper to fail:

  1. the spam rate   Moltbook marks our comments. On 2026-09-27, 128 of
                     u/moltrust-agent's last 166 carried `is_spam` — 77.1 %,
                     at about 24 comments a day. A measurement that only
                     appears in a Sunday report is a post-mortem; here it runs
                     before anything is written, and a bad rate stops the run.
  2. the daily cap   10, down from roughly 24.
  3. relevance       the comment has to be about what we can actually answer,
                     and the reply has to carry a figure or a named
                     specification. The same bar as the reply radar, because
                     the failure was the same: careful, fluent, and about
                     nothing.

The shape is deliberately the radar's. A reply that says something checkable to
someone who asked something checkable is the only kind worth sending, on either
network.

## Why the spam rate cannot simply block at a threshold

77 % is where this starts. If the gate refused to comment until the rate fell,
nothing would ever comment and the rate would never move — the old comments
would hold it hostage for good.

So the rate is measured over the comments made **after the gate went live**,
and while there are too few of those to judge, a small allowance goes out
anyway. That allowance is what produces the evidence the gate then reads.

Between 2026-09-27 09:05 and 2026-09-28 11:30 that allowance produced nothing:
the run wrote no comment in 99 runs, so the post-gate sample stayed at 0 and
`pct` stayed `None`. A rate that is structurally unmeasurable is not a
measurement, so every reading now also carries the rate over the last
`SPAM_WINDOW` comments the account actually has. That figure is always defined,
it goes into the log line of every run, and it is what a human reads when
deciding whether to widen the cap. What blocks a run stays the post-gate scope,
because the hostage problem above has not gone away.
"""
from __future__ import annotations

import datetime
import logging
import os
import re

import httpx

from agents import voice_gate

log = logging.getLogger("comment_gate")

MOLTBOOK_BASE = "https://www.moltbook.com/api/v1"

# Ten a day, from roughly twenty-four.
DAILY_MAX = 10

# Below this the gate lets comments through; at or above it stops the run and
# says so. 25 % is a quarter of everything we write being called spam, which is
# well past "the classifier is twitchy" and into "we are the problem".
SPAM_BLOCK_PCT = 25.0

# How many post-gate comments must exist before the rate means anything. Under
# this the gate is in its probe phase: PROBE_MAX a day, so evidence accrues
# without a bad run costing much.
SPAM_MIN_SAMPLE = 10
PROBE_MAX = 3

# How many of the account's own most recent comments the reported rate covers.
# This one is always defined, unlike the post-gate rate, and it is the figure a
# human reads before widening the cap.
SPAM_WINDOW = 100

# How often one comment may cost a draft before it is given up on. A blocked
# draft leaves the comment open for the next run (ambassador.cmd_run), so
# without a ceiling a question the gate will never pass would draw a fresh draft
# every thirty minutes for as long as it stays in the thread.
GATE_MAX_ATTEMPTS = 3

# Attempt counters for comments nobody has settled yet. Bounded, because the
# state file is rewritten whole on every run.
MAX_TRACKED_ATTEMPTS = 200

# Rule (f) caps a part at 280 characters, because the rules are written for X.
# A Moltbook comment of 1500 characters is ordinary, and the tweet limit
# blocked every reply the ambassador wrote on its first armed run. The number
# is a ceiling against a wall of text, not a platform limit.
MAX_COMMENT_CHARS = 2000

# What we have something to say about. Everything else is someone else's
# conversation and we would be the account that turns up uninvited.
#
# The first version asked for one phrase out of a short list, and the list was
# narrow enough to throw away the conversations the account was already having:
# over the 1268 comments on the 79 posts this account has commented on, it
# admitted 291, and of the 25 comments the agent itself chose to answer it
# admitted 6. Two documented exclusions are in the tests — a comment opening on
# "Verifiable, not trusted" and one about the cooperative equilibrium in
# repeated play, both squarely our subject, both refused because neither says
# "verifiable credential" or "agent trust" in those words.
#
# So two tiers. A strong term carries a comment on its own. The weak terms are
# ordinary English that only means something here in company, so WEAK_MIN
# distinct ones are needed. On the same 1268 comments that admits 625.
ON_TOPIC_STRONG = re.compile(
    r"\b("
    r"agent[- ]?(identity|identities|authorization|authorisation|trust|credential)"
    r"|erc[- ]?8004|x402|did:|dids?|decentrali[sz]ed identifier"
    r"|verifiab\w+|credential\w*|attest\w+|provable"
    r"|know your agent|kya|agent passport|agent registry"
    r"|sybil|trustless|relying party|verifier|cold[- ]?start"
    r"|revo(?:ke|ked|cation)|delegat\w+|mandate|provenance|lineage"
    r"|reputation\w*|signatur\w+|keypair|key rotation"
    r"|equilibri\w+|repeated play"
    r")\b", re.I)

ON_TOPIC_WEAK = re.compile(
    r"\b("
    r"audit\w*|proofs?|prove[nsd]?|proving|identit(?:y|ies)|authenticat\w+"
    r"|authoris\w+|authoriz\w+|permission\w*|capabilit\w+|scope[sd]?"
    r"|incentive\w*|trust\w*|registr(?:y|ies|ation)|principal|issuer|revok\w*"
    r")\b", re.I)

WEAK_MIN = 3

# While the probe runs, a comment is answered only when it puts a direct
# question to us. The account carries a 70 % spam mark and has three comments a
# day to earn a better one, so the three go to the cases where somebody asked.
# The rule carries its own end: after this moment `worth_answering` is the
# relevance path alone, with no code change and no deploy.
DIRECT_QUESTION_UNTIL = datetime.datetime(2026, 9, 30, 23, 59,
                                          tzinfo=datetime.timezone.utc)

# A sentence that ends in a question mark and is built as a question. "Right?"
# is not one, and neither is a statement that happens to end in "?".
_QUESTION_WORD_RE = re.compile(
    r"\b(who|what|when|where|why|how|which|whose|whom"
    r"|do|does|did|is|are|was|were|can|could|would|should|will|shall|have|has"
    r"|any|anyone|is there|are there)\b", re.I)

# "…, right?" asks us to confirm, and carries none of the words above.
_TAG_QUESTION_RE = re.compile(
    r",\s*(right|correct|no|yes|true|isn't it|aren't they|or not)\s*\?\s*$", re.I)

# Does the comment ask for a figure? Rule (f) wants a number in every draft,
# which is right for a question about a rate or a measurement and wrong for one
# about where authority ends. See `needs_number`.
ASKS_FOR_NUMBER_RE = re.compile(
    r"\b("
    r"how (?:many|much|often|long|fast|big|large)"
    r"|what (?:percentage|percent|share|fraction|proportion|rate|number|size|threshold)"
    r"|how do you measure|how would you measure"
    r"|benchmark\w*|measur\w+|quantif\w+|latency|throughput|ratio|sample size"
    r"|per ?cent\w*|\d+\s*%|cost per|price per|orders of magnitude"
    r"|numbers?|figures?|data points?|statistics|baseline"
    r")\b", re.I)

# Nothing that names us goes out. The gate's own rule (d) only guards openers.
PRODUCT_RE = re.compile(r"\b(moltrust|moltguard|moltproof|moltbook|molt)\b", re.I)


def today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


# ── the spam rate ──

def _our_comments(key: str, limit: int = 100) -> list[dict] | None:
    """Our own recent comments, newest first. None when the read fails.

    A failed read is not a clean rate: it has to stop the run rather than be
    counted as zero spam.
    """
    # /agents/me/comments, the same endpoint scripts/sm_kpis.py reads for the
    # Sunday spam figure. The first version of this asked /comments?author=…,
    # which Moltbook answers with an error — and the gate then refused every
    # run for the right reason on the wrong grounds.
    if not key:
        log.error("no Moltbook key — cannot read our own comments")
        return None
    try:
        r = httpx.get(f"{MOLTBOOK_BASE}/agents/me/comments",
                      params={"limit": limit},
                      headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except Exception as e:
        log.error(f"moltbook comment read failed: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        log.error(f"moltbook comments -> {r.status_code}: {r.text[:180]}")
        return None
    data = r.json()
    rows = data.get("comments") if isinstance(data, dict) else data
    return rows if isinstance(rows, list) else None


def _parsed(stamp: str) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def spam_state(state: dict, key: str) -> dict:
    """The rate over post-gate comments, and what it means for this run.

    Returns `mode`: "probe" while the sample is too small to judge, "ok" below
    the threshold, "blocked" at or above it, "unreadable" when Moltbook could
    not be asked.
    """
    live_at = _parsed(state.get("gate_live_at", "")) or datetime.datetime.now(
        datetime.timezone.utc)
    rows = _our_comments(key)
    if rows is None:
        return {"mode": "unreadable", "pct": None, "sample": 0,
                "observed_pct": None, "observed_sample": 0, "observed_spam": 0}

    # What the account looks like right now, whenever the gate went live. Always
    # defined when Moltbook answered at all, which is the point: the post-gate
    # rate below can sit at sample 0 for days.
    window = rows[:SPAM_WINDOW]
    obs_spam = sum(1 for c in window if c.get("is_spam"))
    observed = {"observed_sample": len(window), "observed_spam": obs_spam,
                "observed_pct": round(100.0 * obs_spam / len(window), 1) if window else None}

    scope = [c for c in rows if (_parsed(c.get("created_at") or "") or live_at) >= live_at]
    sample = len(scope)
    if sample < SPAM_MIN_SAMPLE:
        return {"mode": "probe", "pct": None, "sample": sample, **observed}
    spam = sum(1 for c in scope if c.get("is_spam"))
    pct = round(100.0 * spam / sample, 1)
    return {"mode": "blocked" if pct >= SPAM_BLOCK_PCT else "ok",
            "pct": pct, "sample": sample, "spam": spam, **observed}


def observed_note(reading: dict) -> str:
    """The always-defined rate, for the log line of every run."""
    if reading.get("observed_pct") is None:
        return ""
    return (f"; {reading['observed_pct']} % spam over the last "
            f"{reading['observed_sample']} written")


def run_allowance(state: dict, key: str) -> tuple[int, str, dict]:
    """(how many comments this run may post, why, the spam reading).

    Zero with a reason is the normal outcome of a bad day, not an error.
    """
    reading = spam_state(state, key)
    used = int((state.get("comments_per_day") or {}).get(today(), 0))

    if reading["mode"] == "unreadable":
        return 0, "Moltbook's own comment list could not be read — not commenting blind", reading
    if reading["mode"] == "blocked":
        return (0, f"{reading['pct']} % of our last {reading['sample']} comments are "
                   f"marked spam (limit {SPAM_BLOCK_PCT} %)", reading)
    cap = PROBE_MAX if reading["mode"] == "probe" else DAILY_MAX
    room = max(0, cap - used)
    note = observed_note(reading)
    if room == 0:
        return 0, f"daily cap reached ({used}/{cap}){note}", reading
    label = ("probe phase" if reading["mode"] == "probe"
             else f"{reading['pct']} % spam")
    return room, f"{room} left today ({used}/{cap}, {label}){note}", reading


def count_comment(state: dict) -> None:
    per_day = state.setdefault("comments_per_day", {})
    day = today()
    per_day[day] = int(per_day.get(day, 0)) + 1
    state["comments_per_day"] = {k: v for k, v in per_day.items() if k >= day}


def arm(state: dict) -> None:
    """Stamp when the gate first ran, so the rate has a start."""
    state.setdefault("gate_live_at",
                     datetime.datetime.now(datetime.timezone.utc).isoformat())


# ── relevance ──

def asks_a_direct_question(comment_text: str) -> bool:
    """Whether any sentence in the comment is a question put to us."""
    for part in re.split(r"(?<=[?!.])\s+", (comment_text or "").strip()):
        part = part.strip()
        if not part.endswith("?"):
            continue
        if _QUESTION_WORD_RE.search(part) or _TAG_QUESTION_RE.search(part):
            return True
    return False


def worth_answering(comment_text: str,
                    now: datetime.datetime | None = None) -> tuple[bool, str]:
    """Whether the comment is about something we can answer with a fact.

    The reason names what carried the comment, or which rule refused it, so a
    run that answers something odd — or answers nothing — can be traced to the
    rule that decided it.
    """
    text = (comment_text or "").strip()
    if len(text.split()) < 6:
        return False, "too short to carry a question"
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now <= DIRECT_QUESTION_UNTIL and not asks_a_direct_question(text):
        return False, ("no direct question (probe rule, expires "
                       f"{DIRECT_QUESTION_UNTIL:%Y-%m-%d %H:%M} UTC)")
    strong = ON_TOPIC_STRONG.search(text)
    if strong:
        return True, f"on topic ({strong.group(0).lower()})"
    weak = sorted({m.group(0).lower() for m in ON_TOPIC_WEAK.finditer(text)})
    if len(weak) >= WEAK_MIN:
        return True, "on topic (" + ", ".join(weak[:WEAK_MIN]) + ")"
    return False, "not about agent trust"


def needs_number(comment_text: str) -> bool:
    """Whether a reply to this comment has to carry a figure.

    Rule (f) blocks a draft with no digit in it. Asked how large the share is,
    a reply without a number answers nothing and the rule is right. Asked where
    a delegation's authority stops, the honest answer is a sentence about scope,
    and on 2026-09-27 the rule threw away both replies the agent had — one for
    "no concrete number anywhere" over a question that had asked for none.
    """
    return bool(ASKS_FOR_NUMBER_RE.search(comment_text or ""))


def banned_hits(text: str) -> list[str]:
    """The anti-KI-Sprech words in a draft, for naming them back to the model."""
    return voice_gate.banned_words_in(text)


# ── attempts per comment ──

def _attempts(state: dict) -> dict:
    tries = state.get("gate_attempts")
    if not isinstance(tries, dict):
        tries = {}
        state["gate_attempts"] = tries
    return tries


def note_attempt(state: dict, cid: str) -> int:
    """Count one blocked draft for this comment and return the running total."""
    tries = _attempts(state)
    tries[cid] = int(tries.get(cid, 0)) + 1
    if len(tries) > MAX_TRACKED_ATTEMPTS:
        # Insertion order: the oldest counters belong to comments that have
        # scrolled out of the posts we still read.
        for old in list(tries)[:len(tries) - MAX_TRACKED_ATTEMPTS]:
            tries.pop(old, None)
    return tries[cid]


def attempts_left(state: dict, cid: str) -> int:
    return max(0, GATE_MAX_ATTEMPTS - int(_attempts(state).get(cid, 0)))


def clear_attempt(state: dict, cid: str) -> None:
    """Forget the counter once the comment is settled, either way."""
    _attempts(state).pop(cid, None)


# Rule (f)'s wording for a draft with no digit in it, verbatim, because the
# softening below matches on it and has to stop softening if it ever changes.
NO_NUMBER_HIT = "no concrete number anywhere"


def _drop_number_hit(violations: list[str]) -> list[str]:
    """Rule (f) without its digit requirement, keeping everything else it said.

    (f) reports several findings in one line, so the length ceiling and the
    missing digit arrive together. Only the digit finding is dropped, and only
    when the line has the shape the rule writes; anything else passes through
    untouched, so a changed format leaves the draft blocked rather than waved on.
    """
    kept = []
    for v in violations:
        head, sep, hits = v.partition(" — ")
        if not sep or not head.startswith("g2f") or NO_NUMBER_HIT not in hits:
            kept.append(v)
            continue
        rest = [h for h in hits.split("; ") if h.strip() != NO_NUMBER_HIT]
        if rest:
            kept.append(f"{head} — " + "; ".join(rest))
    return kept


def check_reply(text: str, sources: dict[str, str] | None = None,
                require_number: bool = True) -> tuple[bool, list[str]]:
    """Both gates over the drafted comment, in reply mode.

    `sources` is passed straight to rule (h): a claim has to appear in a page
    the draft named. An empty mapping means nothing was fetched, and (h) then
    blocks any claim — which is the intended answer when we cite from memory.

    `require_number` False drops rule (f)'s digit requirement for this draft.
    Pass `needs_number(comment_text)` — a reply owes a figure to a question that
    asked for one. Everything else (f) checks still applies either way.
    """
    problems = []
    if PRODUCT_RE.search(text or ""):
        problems.append("names one of our products")
    try:
        result = voice_gate.scan([text], mode="reply", sources=sources or {},
                                 max_chars=MAX_COMMENT_CHARS)
    except Exception as e:
        # A gate that cannot load its rules must not wave the comment through.
        return False, [f"voice gate unavailable: {type(e).__name__}: {e}"]
    found = list(result.get("violations") or [])
    if not require_number:
        found = _drop_number_hit(found)
    problems += found
    return not problems, problems
