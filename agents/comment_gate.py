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

# Two numbers, because one was doing two jobs badly. 120 is what the
# instruction asks for and what a good reply looks like: over the run of
# 2026-09-28 the median came in at 119. 150 is where the draft is refused. At a
# single limit of 120 the rule threw away 20 of 34 refusals, more than any voice
# rule, for drafts that were long rather than wrong.
TARGET_COMMENT_WORDS = 120
MAX_COMMENT_WORDS = 150

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

# ── c-rules: what the draft may assert ────────────────────────────────────────
#
# The voice gate judges how a sentence is built. These judge whether it is
# entitled to say what it says. On 2026-09-28 a draft that satisfied every g-rule
# opened with:
#
#     Checking agent trust score for EkremAI...
#     Score: 67 (trusted, substantive contributor)
#     Proceeding with reply.
#
# A tool this process does not have, a figure that tool would have returned, and
# a rating of somebody else's account, all invented. We sell agent trust scoring.
# A published score we did not compute refutes the product in the act of
# demonstrating it, which is why this is its own class of rule rather than a
# formatting finding: the draft was well-formed and still unpublishable.

# A verdict on the agent we are answering: a score, a rating, a trust level.
C1_VERDICT_RE = re.compile(
    r"^[ \t]*(trust[ \t-]*score|score|rating|trust[ \t]*level|confidence|"
    r"reputation[ \t]*score)[ \t]*[:=][ \t]*\S"
    r"|\b(your|their|this agent'?s?|the agent'?s?)[ \t]+"
    r"(trust[ \t-]*score|reputation[ \t]*score|rating|trust[ \t]*level)\b"
    r"[^.\n]{0,40}?\b(is|sits at|comes out at|of)\b[ \t]*\d",
    re.I | re.M)

# The model narrating a tool it has not got, or the output such a tool would give.
C2_TOOL_RE = re.compile(
    r"^[ \t]*(checking|check|proceeding|analy[sz]ing|fetching|retrieving|"
    r"looking up|querying|consulting|running|calling|verifying|validating|"
    r"loading|computing)\b[^\n]*\.\.\.[ \t]*$"
    r"|^[ \t]*(verified|unverified|result|status|output|tool|lookup|"
    r"score|confidence)[ \t]*[:=][ \t]*\S",
    re.I | re.M)

# Which figures a draft may carry. Anything with a digit in it has to appear in
# the comment being answered or in one of our own fetched pages; there is no
# third source in this process.
_FIGURE_RE = re.compile(r"\d[\d.,:/'’_-]*")


def _figures(text: str) -> set[str]:
    """Digit groups, separators removed, so $10,000 and 10000 compare equal."""
    out = set()
    for m in _FIGURE_RE.finditer(text or ""):
        token = re.sub(r"[^\d]", "", m.group(0))
        if token:
            out.add(token.lstrip("0") or "0")
    return out


def ungrounded_figures(draft: str, comment_text: str = "",
                       sources: dict[str, str] | None = None) -> list[str]:
    """Figures in the draft that are in neither the comment nor our own pages."""
    grounded = _figures(comment_text)
    for page in (sources or {}).values():
        grounded |= _figures(page)
    return sorted(_figures(draft) - grounded, key=len)


# c5: a first sentence whose job is to tell them their contribution was good.
#
# Rule g1b catches the literal openers — "good point", "you're right" — from a
# lexicon of 48 phrases in moltrust-web. It cannot catch the shape, and the shape
# is what got through on 2026-09-28: "You've isolated something the identity
# layer doesn't touch." and "The audit trail you're building is the move."
#
# The line drawn here is the second person. A verdict on the subject is ordinary
# argument and stays allowed — "Scope-binding per escalation move is the right
# constraint" opens on the mechanism. A verdict on *them*, about what *they*
# did, is the opener the instruction forbids. Requiring both halves is what
# keeps the neutral openers out of it.
_SECOND_PERSON_RE = re.compile(r"\b(you|you'?re|you'?ve|your|yours)\b", re.I)

_PRAISE_RE = re.compile(
    r"\b(nailed|isolated|separated|identified|spotted|captured|articulated"
    r"|pinpointed|zeroed in|put your finger on|hit on|hit upon|got (?:it|this) right"
    r"|cut (?:to|at|through)|are onto|is onto|touched on|named something)\b"
    r"|\b(?:is|are|reads as|sounds like)\s+(?:the|a|an)\s+"
    r"(?:right|real|sharp|key|hard|core|crucial|important|interesting|correct)\b"
    r"|\bis\s+the\s+(?:move|crux|point|question|thing|insight|one)\b"
    r"|\b(?:exactly|precisely)\s+right\b|\bspot on\b|\bwell put\b"
    r"|\b(?:sharp|brilliant|excellent|elegant|clever)\s+"
    r"(?:framing|point|question|catch|read|decomposition|observation)\b", re.I)


def evaluative_opener(draft: str) -> str:
    """The opening sentence if it praises what the other agent did, else ""."""
    first = ""
    for part in re.split(r"(?<=[.!?])\s+", (draft or "").strip()):
        if part.strip():
            first = part.strip()
            break
    if not first:
        return ""
    if _SECOND_PERSON_RE.search(first) and _PRAISE_RE.search(first):
        return first[:90]
    return ""


def invented_claims(draft: str, comment_text: str = "",
                    sources: dict[str, str] | None = None) -> list[str]:
    """The c-rules a draft breaks, as violation lines with their own codes."""
    problems = []
    if C1_VERDICT_RE.search(draft or ""):
        problems.append("c1 Erfundene Bewertung — a score or rating for the agent "
                        "we are answering, which this process cannot compute")
    if C2_TOOL_RE.search(draft or ""):
        problems.append("c2 Nachgespielter Werkzeugaufruf — a tool call or status "
                        "line the model narrated; there is no tool here")
    loose = ungrounded_figures(draft, comment_text, sources)
    if loose:
        problems.append("c3 Ungedeckte Zahl — not in the comment and in none of "
                        "our pages: " + ", ".join(loose[:6]))
    return problems


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


# ── i-rules: which comments are answered at all ───────────────────────────────

# i1: an advert wearing a comment. On 2026-09-28 the run would have answered
# jb_aux_pe, whose comment closes "Try AUX on your next tx: <link>". Replying
# hands a stranger's product our reach and puts our account in their thread.
# Both halves are required — a link on its own is often a citation.
_INBOUND_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+|\b\S+\.(?:ai|io|com|net|xyz|app|co)/\S*",
                             re.I)
_INBOUND_CTA_RE = re.compile(
    r"\b(try|check (?:it )?out|sign up|get started|join|visit|grab|claim|"
    r"download|install|use code|dm me|hit me up|book a|start (?:your|a) free)\b"
    r"|\b(?:early|limited|beta)\s+access\b", re.I)

# i2: the substance floor on the incoming comment. Measured over the 100-comment
# corpus of 2026-09-28: the ten comments worth a reply carried 28 to 50 content
# words; the one that was not carried 11, and answering it would have spent a
# probe slot on "That The same delegation problem shows is exactly the kind of
# detail that compounds." Twelve is the smallest floor that separates them, so
# it is the one that throws away least: four of the hundred.
MIN_CONTENT_WORDS = 12
MIN_SENTENCE_WORDS = 6

_WORD_RE = re.compile(r"[a-z'’]{3,}", re.I)
_STOPWORDS = frozenset("""
the a an and or but if then of to in on at for with by from as is are was were
be been being this that these those it its i we you they he she them us our
your their not no so such very much many more most some any all can could
would should will shall have has had do does did there here what which who
whom whose how when where why just also too only own same than once about into
over under again further
""".split())


def content_words(text: str) -> int:
    """Distinct words that carry something, so repetition does not pad a comment."""
    return len({w.lower() for w in _WORD_RE.findall(text or "")
                if w.lower() not in _STOPWORDS})


def has_complete_sentence(text: str) -> bool:
    """At least one sentence that ends properly and is long enough to say something."""
    for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        part = part.strip()
        if part.endswith((".", "!", "?")) and len(part.split()) >= MIN_SENTENCE_WORDS:
            return True
    return False


def is_promotion(comment_text: str) -> bool:
    """A link and a call to action in the same comment."""
    text = comment_text or ""
    return bool(_INBOUND_URL_RE.search(text) and _INBOUND_CTA_RE.search(text))


def worth_answering(comment_text: str,
                    now: datetime.datetime | None = None) -> tuple[bool, str]:
    """Whether the comment is about something we can answer with a fact.

    The reason carries a rule code, so a run that answers something odd — or
    answers nothing for a day — can be traced to the rule that decided it.
    """
    text = (comment_text or "").strip()
    if len(text.split()) < 6:
        return False, "i0 Zu kurz — too short to carry a question"
    if is_promotion(text):
        return False, "i1 Werbung — a link and a call to action, not a question"
    if not has_complete_sentence(text):
        return False, ("i2 Substanzboden — no sentence of at least "
                       f"{MIN_SENTENCE_WORDS} words that ends")
    asked = asks_a_direct_question(text)
    # A question carries its own reason to exist and is short by nature: "How
    # does agent identity survive crossing an org boundary?" has six content
    # words and is worth every one of them. A comment that asserts instead has
    # to bring something to assert.
    carried = content_words(text)
    if not asked and carried < MIN_CONTENT_WORDS:
        return False, (f"i2 Substanzboden — {carried} content words and no "
                       f"question, the floor is {MIN_CONTENT_WORDS}")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now <= DIRECT_QUESTION_UNTIL and not asked:
        return False, ("i3 Probe-Regel — no direct question (expires "
                       f"{DIRECT_QUESTION_UNTIL:%Y-%m-%d %H:%M} UTC)")
    strong = ON_TOPIC_STRONG.search(text)
    if strong:
        return True, f"on topic ({strong.group(0).lower()})"
    weak = sorted({m.group(0).lower() for m in ON_TOPIC_WEAK.finditer(text)})
    if len(weak) >= WEAK_MIN:
        return True, "on topic (" + ", ".join(weak[:WEAK_MIN]) + ")"
    return False, "i4 Kein Thema — not about agent trust"


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


def redraft_note(draft: str) -> str:
    """What to tell the model about the draft it just handed back, or "".

    Two faults earn a second attempt, because both are repairs to wording rather
    than to the answer: a word off the banned list, and a draft over the word
    limit. Everything else settles the attempt where it stands — a draft that
    invents a score is not one redraft away from being right.
    """
    notes = []
    words = banned_hits(draft)
    if words:
        notes.append("it used " + ", ".join(f"'{w}'" for w in words)
                     + ", which are banned words. Write it again without any of "
                     "them, and without a synonym filling the same slot.")
    length = len((draft or "").split())
    if length > MAX_COMMENT_WORDS:
        notes.append(f"it ran to {length} words. The draft is refused over "
                     f"{MAX_COMMENT_WORDS} and the target is "
                     f"{TARGET_COMMENT_WORDS}. Cut it to {TARGET_COMMENT_WORDS} "
                     f"by dropping whole sentences, not by compressing every "
                     f"one of them.")
    if not notes:
        return ""
    return ("Your previous draft was refused: " + " Also, ".join(notes))


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
                require_number: bool = True,
                comment_text: str = "") -> tuple[bool, list[str]]:
    """Every rule over the drafted comment, in reply mode.

    `sources` is passed straight to rule (h): a claim has to appear in a page
    the draft named. An empty mapping means nothing was fetched, and (h) then
    blocks any claim — which is the intended answer when we cite from memory.

    `require_number` False drops rule (f)'s digit requirement for this draft.
    Pass `needs_number(comment_text)` — a reply owes a figure to a question that
    asked for one. Everything else (f) checks still applies either way.

    `comment_text` is what the draft answers, and it is the other place a figure
    may come from under c3. Left empty, every figure in the draft is ungrounded
    unless one of our own pages carries it.
    """
    problems = []
    if PRODUCT_RE.search(text or ""):
        problems.append("c0 Produktnennung — names one of our products")
    problems += invented_claims(text, comment_text, sources)
    words = len((text or "").split())
    if words > MAX_COMMENT_WORDS:
        problems.append(f"c4 Überlänge — {words} words, the limit is "
                        f"{MAX_COMMENT_WORDS} and the target is "
                        f"{TARGET_COMMENT_WORDS}")
    opener = evaluative_opener(text)
    if opener:
        problems.append("c5 Bewertende Eröffnung — the first sentence judges "
                        f"what they did rather than opening on the subject: {opener}")
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
