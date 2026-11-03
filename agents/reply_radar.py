"""Reply radar — drafts replies, and hands most of them to a human.

**From 2026-10-18 it drafts nothing.** Lars decided on 12.10.2026 (BW1) to
retire the reply branch: replies on X run under his own account, by hand, with
no cadence and no obligation. From that date the radar is a reading list — the
same sources, the same filters, one Telegram message per run with the posts
that passed and why, no draft, no button, no X write. The gate is
REPLY_BRANCH_RETIRED_FROM below; docs/reply-radar.md has the decision and
docs/reply-radar-closing-2026-10.md the series it rests on. What follows
describes the branch as it ran until then.

Every two hours it reads three sources, picks what is worth answering, drafts a
reply for each, runs both gates over it, and sends the survivors to the stats
channel with a button under each.

Every draft carries the same two keys (CM2), and where the post came from
decides only what the press can do:

    "✅ Senden"    — a callback. For a mention the consumer re-runs both
                  gates against freshly fetched sources and posts through the
                  API; for anything else the press answers that X refuses a
                  third-party reply and hands the text back to copy.
    "🗑 Verwerfen" — a callback. Answered with one question: four short
                  keys and free text; no answer in twelve hours is "ohne
                  Grund" (CM6). Every draft ends as one line in
                  data/reply_radar_decisions.jsonl (agents/reply_decisions.py).

Twelve hours after the message went out, Senden sends nothing and says so
(DRAFT_TTL_HOURS). While the send side is retired the message carries no key
at all — see CM14 below.

X closed API replies to third-party posts in February 2026, on every tier:

    403 — You can only reply to or quote posts where you are mentioned
          or are the author.

A mention is ours to answer because we are named in it. A post from the targets
list is not, and no access level changes that, so the radar stopped pretending
otherwise: those drafts never reach POST /2/tweets. `REPLY_RADAR_ARMED` now
arms exactly one path, the mention path.

Handing a draft over does not end the measurement. Every fifteen minutes the
consumer reads our own timeline and matches its replies against the drafts it
handed out; a hit rewrites the Telegram message to "✅ Gepostet <link>" and
feeds the same counters and the same `kind: "reply"` series in
digest_metrics.py that an API post would have.

Sources, in the order they are trusted:

    1. the owned `targets` list on @moltrust — curated, highest signal
    2. search/recent over a fixed query set — wider, noisier
    3. our own mentions — someone already spoke to us

Three rules, from the brief, and the first two are enforced rather than asked
for:

    no link      gate 2 (e) in mode="reply" expects zero links
    no pitch     no product name anywhere in the draft, not just the opener
    a number     gate 2 (f) already requires one

    a source    gate 2 (h) — every checkable claim must appear in a page the
                draft named and this run fetched

A draft that carries a counterexample instead of a number is blocked and shows
up in Telegram anyway, which is where every draft goes today. That is the
intended failure: a counterexample nobody can state with a figure or a named
spec is usually an opinion, and an opinion is what the reply radar exists to
not send.

Rule (h) is why the drafter returns JSON rather than prose. It has to name the
pages it took its figures from; the radar fetches them and the gate looks for
each claim in the text. A remembered citation and an invented one are
indistinguishable in a reply, so neither is allowed through on trust.

    python agents/reply_radar.py              # the scheduled run
    python agents/reply_radar.py --dry-run    # print, send nothing
    python agents/reply_radar.py --limit 3    # fewer drafts this run
"""
from __future__ import annotations

import datetime
import html
import json
import logging
import os
import re
import sys
import traceback
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import requests
from requests_oauthlib import OAuth1

from app import notify, telegram_inbox
from agents import reply_decisions, voice_gate, x_meter, x_post

DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")
STATE_FILE = os.path.join(DATA_DIR, "reply_radar_state.json")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "reply_radar_heartbeat.json")

OUR_USER_ID = "2023702578836779008"          # @moltrust
TARGETS_LIST_ID = "2101805022954557791"      # private list, empty until approved
MODEL = "claude-opus-5"

# Two a day from 22.10.2026 (Lars, CM4), read from the one place both radar
# numbers live: x_meter.DAILY_DRAFT_CAP. It was 8 from the brief, then zero
# from 18.10 (CJ1) when the branch failed its threshold
# (docs/reply-radar-closing-2026-10.md).
#
# This is the cap, not the switch. The branch itself stays retired —
# reply_branch_retired() ends every run before the cap is read, and turning it
# back on is CM1, after the rest of the CM chain is accepted. The reading list
# does not use this number.
DAILY_MAX = x_meter.DAILY_DRAFT_CAP
PER_RUN_MAX = 3          # twelve runs a day, so this is a ceiling, not a target
# Fallback only. The window is normally "since the last run, plus half an
# hour" — a fixed three hours against a four-hour cadence re-read an hour of
# posts every run, and 39 of one run's candidates were dropped as older than
# the lookback while being paid for.
LOOKBACK_HOURS = 4
LOOKBACK_OVERLAP_MINUTES = 30
# 12 -> 15 on 11.10.2026, with the 16:05 run (BN4). The 06:05 run now follows
# 16:05, fourteen and a half hours with the overlap; at 12 the stretch from
# 16:05 to 18:05 was read by no run at all.
LOOKBACK_MAX_HOURS = 15

# Applies to search and mention hits only. A list member was curated by hand;
# making it clear a second numeric bar would be curating twice.
#
# Raised from 25 to 200 on 23.09.2026. At 25 the radar was drafting careful,
# sourced replies under posts nobody had read — the first three live replies
# drew 6, 8 and 0 impressions under targets with 2,870, 1,494 and 9,162. The
# floor is not about the post's quality, it is about whether a reply there can
# be seen at all.
#
# Lowered to 100 the same day, after measuring it: of 159 posts in a three-hour
# window, 200 left five candidates and every one of them came from the list.
# A floor that closes the search leg entirely is not a floor, it is a switch.
#
# 100 -> 50 on 27.09.2026. One step, to see whether the search leg produces
# anything worth posting before the branch comparison is read on 04.10.
MIN_IMPRESSIONS = 50

# Same run, same reason: an account this small cannot carry a reply into
# anyone's timeline. List members are exempt — they were curated by hand, and
# the ones that matter most to us are the small ones.
MIN_AUTHOR_FOLLOWERS = 500

# One draft per author per run, two per day. Without this the ranking hands
# every slot to whoever posts most: on 23.09 all five surviving candidates in a
# three-hour window came from one account. Replying three times a day to the
# same person is not a radar, it is a habit.
#
# Mentions are exempt. Someone who addressed us directly is owed an answer, and
# a cap there would mean ignoring the second thing they said.
PER_AUTHOR_PER_RUN = 1
PER_AUTHOR_PER_DAY = 2

# Two post shapes that are never worth a reply, whatever they say.
#
# A cashtag is a price conversation. Whatever we could add about agent identity
# lands in a thread about a number going up, and the reply reads as promotion
# by association.
CASHTAG_RE = re.compile(r"(?<![\w$])\$[A-Za-z]{2,6}\b")

# "3/7" and "3 of 7" mark one instalment of somebody's thread. Replying to the
# middle of one answers a sentence the author has not finished, and the thread
# usually already contains what we would have said.
THREAD_COUNTER_RE = re.compile(r"(?:^|\s)\(?\d{1,2}\s*(?:/|of)\s*\d{1,2}\)?(?:\s|$)")

TARGETS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "reply_targets.json")

# Tier 4 and anyone over a million followers is only worth a reply when the post
# is about our actual subject. Everything else they post is somebody else's
# conversation and we would be the account that turns up uninvited.
BIG_ACCOUNT_FOLLOWERS = 1_000_000
# Widened 27.09.2026. The old set was so narrow that a post about agent
# payouts or an escrow condition read as off topic, while the accounts the rule
# exists to keep out — the news feeds — are kept out by the topic requirement
# itself, not by the vocabulary being small.
ON_TOPIC_RE = re.compile(
    r"\b(agent[- ]?(identity|identities|authorization|authorisation|trust|"
    r"credential|reputation|passport|registry)"
    r"|erc[- ]?8004|x402"
    r"|did:|\bdid\b|decentrali[sz]ed identifier"
    r"|verifiable credential|attestation|attested"
    r"|know your agent|\bkya\b"
    r"|agent (payments?|payouts?)"
    r"|conditional payment|escrow"
    r"|mandate|delegation chain"
    r"|verification|provenance)\b", re.I)

# Nothing that names us may go out. Gate 2 (d) only guards the opener.
PRODUCT_RE = re.compile(r"\b(moltrust|moltguard|molcheck|moltproof|moltbook|molt)\b", re.I)

# ERC-8004 only since 11.10.2026 (Lars, BN6). In the 14 days to 10.10 the
# search cost about $5.40 per draft that passed the gates against $1.25 from
# the list, and both of its passing drafts came from this query. The other four
# brought 23 candidates and none that passed.
SEARCH_QUERIES = [
    '("ERC-8004" OR "erc8004") -is:retweet lang:en',
]

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("reply_radar")
notify.silence_http_request_logs()
os.makedirs(DATA_DIR, exist_ok=True)


SYSTEM_PROMPT = """You draft replies for @moltrust on X.

A reply earns its place by adding something the thread does not have: a figure,
a named specification, or a case that points the other way. Anything else is
noise with our name on it.

**One core claim, on the subject of the post you are answering.** Not two, not
one plus an aside. The reader has to be able to say what the reply asserts in a
sentence. A reply that starts on the post's subject and then arrives at a
subject of ours reads as an advertisement, however true both halves are.

**Our own themes only when the post raises them.** If the post is about a
spoofable access check, answer about that check. Do not reach for our blog, our
measurements or our standards work because they are adjacent. If the post does
not raise the subject, the subject does not belong in the reply.

Hard rules, all of them enforced after you write:
- One reply, at most 275 characters.
- No link. Not ours, not anyone's.
- Never mention MolTrust, MoltGuard, MoltProof or any product of ours. Not as a
  recommendation, not as a disclosure, not in passing. If the only thing you
  have to say is that we built something for this, say nothing.
- Carry a concrete number, or a named specification or document with a number
  in it (ERC-8004, RFC 8785, CVE-2026-...). A reply without one will be blocked.
- **Every such claim must come from a page you name.** Below this prompt you
  are given our own published pages, already fetched, with their URLs. Take
  your figures from those and list the URLs you used.
- **The post you are answering counts as a source.** A figure it states is one
  you may use and should prefer, because it is the figure the thread is already
  about. Its links are also fetched if you name them. Nothing else. Do not cite
  a page from memory — a remembered URL and an invented one are
  indistinguishable, which is the whole reason this check exists. If neither
  the post nor the pages below give you a figure you can stand behind, say
  SKIP.
- Our own published measurement is worth using **when it speaks to the post's
  subject**. It is published, dated and anyone can open it — and it is still
  the wrong answer if the post was about something else.
- No hashtags, no emoji, no greeting, no "great point", no thanks.
- No link and no domain in the reply itself. The pages you used go in
  `sources`; they are shown under the draft, never inside the text.
- Do not open by evaluating the post or its author.
- State the thing directly. No "not X but Y" constructions.

If the post does not give you something factual to answer with, or if you
cannot point at a page that carries your figures, return SKIP with its reason,
on one line, in exactly this form:
SKIP: <code> — <one short sentence>
where <code> is one of: promotion, price-talk, no-checkable-claim, no-source,
off-subject, repeat-figure, other.

Otherwise return strict JSON and nothing else:
{"reply": "the reply text", "sources": ["https://...", "https://..."]}"""


# ── State ──

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"State write failed: {e}")


def write_heartbeat(status: str, detail: str = "") -> None:
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump({"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "status": status, "detail": detail}, f)
    except Exception:
        pass


def drafted_today(state: dict, today: str) -> int:
    return int(state.get("per_day", {}).get(today, 0))


# How long a core claim is spent once a draft has used it. The old guard kept
# the last twelve claims with no clock, and looked for numbers of three digits
# or more — so "EU AI Act Article 12" was invisible to it and led all three
# drafts of the 14:00 run on 22.09.2026.
CLAIM_WINDOW_HOURS = 48

# What counts as a core claim. Order matters: the act-plus-section alternative
# has to win over the bare section, so "EU AI Act Article 12" and "Article 12"
# of something else do not collapse into one key.
_ACT = (r"(?:EU\s+AI\s+Act|AI\s+Act|GDPR|DSA|DORA|MiCA|NIS2|eIDAS|"
        r"Data\s+Act|Cyber\s+Resilience\s+Act)")
_SECTION = r"(?:Articles?|Art\.?|Artikel|Sections?|Sec\.?|§|Recital|Annex)\s*\d+[A-Za-z]?"
_SPEC = r"(?:RFC|CVE|ERC|EIP|BIP|CWE|ISO|NIST|SLSA|SOC|BCCRT)[-\s]?\d+[\w./-]*"
_MONEY = r"\d[\d.,]*\s?(?:%|USDC|USD|EUR|CHF|GBP)"
# A thousands-separated number is one claim, so it has to be tried before the
# bare run of digits — otherwise "17,000" is read as "000".
_GROUPED = r"\d{1,3}(?:[,.]\d{3})+"

# The bare-number alternative skips four-digit years. "applies from 2 Aug 2026"
# is a date, not a figure, and treating it as a core claim would spend the
# window on every draft that names a deadline.
CLAIM_MARK_RE = re.compile(
    rf"{_ACT}\s+{_SECTION}|{_SECTION}|{_SPEC}|{_MONEY}|\b{_GROUPED}\b"
    rf"|\b(?!(?:19|20)\d{{2}}\b)\d{{3,}}\b", re.I)

# Claims Lars has taken off the table by hand, each with the moment it comes
# back. A file rather than a constant: a block is a judgement about the last
# few days, not about the code.
BLOCKLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "config", "claim_blocklist.json")

# Tell the stats channel once, when the decisions first reach this many. A
# running tally nobody asked for is noise; the first twenty are the sample that
# says whether the drafts are worth anything.
DECISION_REPORT_AT = 20

# The write path. Posting needs --consume and this flag in the environment,
# the same shape as scripts/revoke_inactive.py: a switch that lives only in an
# argument is one edited crontab line away from firing.
ARM_FLAG = "REPLY_RADAR_ARMED"

# X closed API replies to third-party posts in February 2026, on every tier:
#
#   403 — You can only reply to or quote posts where you are mentioned
#         or are the author.
#
# So the radar has two paths that look alike and are not. A mention can still
# be answered through the API, because we are mentioned in it. Everything from
# the list or from search is handed over as a prepared intent link and posted
# by a human; nothing from those sources ever reaches POST /2/tweets.
API_REPLYABLE_SOURCES = {"mention"}
INTENT_URL = "https://x.com/intent/post"

# The reply branch ends on 2026-10-18 (Lars, BW1, decided 12.10.2026). It did
# not clear the threshold fixed on 04.10: 0.25 profile clicks per reply in the
# window against 0.5 (scripts/reply_impact.py, 12.10). Replies on X run under
# Lars's own account from then on, by hand; @MolTrust carries the digest and
# the blog syndication.
#
# A date in the code, not a flag in the crontab, for the reason ARM_FLAG is an
# environment variable and not an argument: a switch that lives in one crontab
# line comes back on when somebody edits that line. From this date the drafter
# is never called, no message carries a draft or a button that leads to a post,
# and --consume refuses every rr|post, including presses on drafts sent before
# the date. Turning it back on is a code change with a PR.
#
# A gate rather than deleting the paths, so the days up to 18.10 run on the
# setup the measurement was read against. The candidate filter keeps running;
# its output becomes a reading list (reading_list_run below).
REPLY_BRANCH_RETIRED_FROM = datetime.date(2026, 10, 18)
RETIRED_REASON = "reply branch retired from 2026-10-18 (Lars, BW1)"

# The drafter comes back on 27.10.2026 (Lars, CM13), the send paths do not.
# Two gates from here on, and the split is the whole point of the change:
#
#   drafter_retired()        the model, the daily cap, the Telegram message
#   reply_branch_retired()   every path that can put text on X
#
# So a draft is written and shown again, and there is still no route from a
# draft to a post: POST /2/tweets stays refused in --consume, and the intent
# link — a post one tap away — stays refused too. What Lars gets is the draft
# as text to copy (CM14), which is what he asked for while the button path
# from CM2 does not exist yet.
DRAFTER_BACK_FROM = datetime.date(2026, 10, 27)
DRAFTS_BACK_REASON = "drafts back from 2026-10-27 (Lars, CM13); sending stays off"

# The intent link comes back on 31.10.2026 (Lars, CM16-CM18) in place of the
# button path. A link is not a send: it opens X on Lars's phone with the target
# post and the draft filled in, and he posts it himself from whatever account
# is signed in. Nothing goes through our credentials, so there is no account to
# unlock and CM3 falls away. reply_branch_retired() keeps guarding the API
# side; this gate opens only the link, and only as text in the message, never
# as a key. Ignoring the message discards the draft (CM17).
INTENT_LINK_BACK_FROM = datetime.date(2026, 10, 31)

# A reading list is read by one person on a phone. Ten posts is more than the
# drafter ever produced in a run; past that the message stops being read.
READ_LIST_MAX = 10


def _utcnow() -> datetime.datetime:
    """The clock the gate reads. One function, so a test can move it."""
    return datetime.datetime.now(datetime.timezone.utc)


def reply_branch_retired(now: datetime.datetime | None = None) -> bool:
    """True from 2026-10-18 00:00 UTC. Every path that can post asks this."""
    return (now or _utcnow()).date() >= REPLY_BRANCH_RETIRED_FROM


def intent_link_open(now: datetime.datetime | None = None) -> bool:
    """True from 31.10.2026 (Lars, CM16). Opens the link, not the API."""
    return (now or _utcnow()).date() >= INTENT_LINK_BACK_FROM


def drafter_retired(now: datetime.datetime | None = None) -> bool:
    """True 18.10 to 26.10.2026, False again from 27.10 (Lars, CM13).

    Only the drafting side. Nothing that can reach X reads this.
    """
    day = (now or _utcnow()).date()
    return REPLY_BRANCH_RETIRED_FROM <= day < DRAFTER_BACK_FROM
# Every half hour, not every quarter. Nobody posts by hand in under a minute,
# and each check is a read against a metered API.
MANUAL_CHECK_MINUTES = 30

# A draft nobody took within a day stops being watched. This was three days,
# which meant three days of timeline reads for an offer that was never going
# to be taken.
MANUAL_PENDING_HOURS = 24

# CM2: a draft decays. Twelve hours after the message went out, Senden sends
# nothing — the post it answers has moved on, the figures in the message are
# stale, and a button that still works the next morning is a button that posts
# yesterday's reply into today's thread. The press is answered, not swallowed:
# the message says the draft expired, and that is the whole answer. No reason
# is asked for, because nobody decided anything (the reason on a discard is
# CM6).
DRAFT_TTL_HOURS = 12

# One page of the list per run. There is no incremental read: X answers
#
#   The query parameter [since_id] is not one of
#   [id, max_results, pagination_token, post.fields]
#
# for /2/lists/:id/tweets. since_id was added on 27.09 on the assumption that
# it worked there, and from the first run that had an id to send, the list
# returned 400 — for about 24 hours, while the runs still looked successful.
#
# Losing it costs nothing. X deduplicates resources within a UTC day, so
# re-reading the same list page later the same day was already free; since_id
# would only have saved money across midnight.
# Twenty-five, down from fifty. The list leg became the biggest single item
# once the search was cut to ten per query: two reads of fifty is ~100 posts a
# day, $0.50, against a $0.80 target for everything. The list is curated and
# the run drafts at most three, so the question is whether twenty-five newest
# posts per read still carry three worth answering — and that is a measurement,
# not a setting. See docs/reply-radar.md, "The bound that is actually binding":
# if the draft rate starts tracking candidate supply rather than the drafter's
# refusals, this number is wrong and the budget, not the radar, has to move.
#
# Back to fifty on 11.10.2026 (Lars, BN3). Measured across the cut: 18.2 list
# candidates a day before 04.10, 7.3 after, and the list delivered six of the
# eight drafts that passed the gates in the 14 days to 10.10 at about $1.25
# each. The one change with a measured before and after.
LIST_PAGE = 50

# Forty from 18.10.2026 (Lars, BX2), when the radar becomes a reading list read
# once a day at 06:05 with no search. Fifty posts at $0.005 is $0.25, which is
# the whole of the $0.25 a day Lars set for the radar without drafter and
# without X writes; mentions come on top. Forty caps the list at $0.20 and
# leaves the mentions inside the line. Gated on the same date as the drafter,
# so the reads up to 17.10 stay on the setup the reply measurement was read
# against.
LIST_PAGE_READING_LIST = 40


def list_page(now: datetime.datetime | None = None) -> int:
    """Page size of the list read: 50 up to 17.10.2026, 40 from 18.10."""
    return LIST_PAGE_READING_LIST if reply_branch_retired(now) else LIST_PAGE

# Ten per query, not twenty-five. X bills per resource returned, and on
# 2026-10-03 one search run came back with 77 posts and 67 author profiles:
# $1.045 of a $1.50 daily breaker, which shut the later runs out every day for
# three days running. Five queries at ten is 50 posts and at most 50 profiles,
# so the worst case halves. The filters then cut it to a handful either way —
# the 15 candidates that reached the drafter on 03.10 came out of 157 posts.
SEARCH_PAGE = 10
MAX_TARGET_AGE_HOURS = 24
DRAFT_RE = re.compile(r"Entwurf \((\d+)/280\):\s*\n(.+?)(?:\n\n|\Z)", re.S)
SOURCE_RE = re.compile(r"^Quelle \(([a-z]+)\):", re.M)
SOURCES_RE = re.compile(r"^· (https?://\S+)$", re.M)


def armed() -> bool:
    return os.getenv(ARM_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def parse_draft(message_text: str) -> tuple[str, list[str]] | None:
    """Pull the draft and its sources back out of the message that was clicked.

    Deliberately read from the message rather than from a stored copy: what
    goes out is then exactly the text the person approved, and there is no
    second version that could have drifted from it.
    """
    m = DRAFT_RE.search(message_text or "")
    if not m:
        return None
    return m.group(2).strip(), SOURCES_RE.findall(message_text or "")


def draft_source(message_text: str) -> str:
    """Which source a sent draft came from, read back out of its own message.

    State would be the other place to keep this, and state can be lost or
    rebuilt; the message the button sits under cannot.
    """
    m = SOURCE_RE.search(message_text or "")
    return m.group(1) if m else ""


def target_as_source(tweet: dict) -> dict[str, str]:
    """The post being replied to, as a citable source for rule (h).

    A reply may lean on what the post it answers actually says. On 02.10 the
    gate blocked a reply carrying "114.09 ETH" as an unsourced claim while the
    post it answered read "@aave v3 Loop Safe Module Loss: ~114.09 ETH" — the
    figure was on screen, it just was not in the corpus (h) searched.

    Only this post's own text. Not the pages it links to: those are fetched
    separately and only when the draft names them, and a post that links
    somewhere must not turn that whole page into something we may quote.
    """
    text = (tweet.get("text") or "").strip()
    if not text:
        return {}
    author = tweet.get("_author") or "i"
    return {f"https://x.com/{author}/status/{tweet['id']} (the post being answered)": text}


def target_ok(auth, tweet_id: str) -> tuple[bool, str, dict]:
    """Does the post we would answer still exist, is it fresh, and what does it say?

    The text comes back so the consumer can give rule (h) the post without
    paying for a second read: X bills per resource returned.
    """
    # Deliberately not behind DAILY_PROFILE_READS. This buys one profile per
    # draft — a handful a day against the 66 a search round brings — and the
    # username it returns builds the post URL rule (h) is checked against.
    # Dropping it to save a cent would put the wrong URL in the scan.
    body = _get(auth, f"https://api.twitter.com/2/tweets/{tweet_id}",
                {"tweet.fields": "created_at,text", "expansions": "author_id",
                 "user.fields": "username"})
    data = body.get("data") if body else None
    if not data:
        return False, "the post is gone or unreadable", {}
    created = data.get("created_at")
    if created:
        when = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() / 3600
        if age > MAX_TARGET_AGE_HOURS:
            return (False,
                    f"the post is {age:.0f}h old, past the {MAX_TARGET_AGE_HOURS}h limit",
                    {})
    users = (body.get("includes", {}) or {}).get("users") or [{}]
    data["_author"] = users[0].get("username", "i")
    return True, "", data


def edit_message(chat_id, message_id, text: str) -> bool:
    """Rewrite one of our messages. True only when Telegram says it did.

    The answer used to be thrown away, so a 400 looked exactly like a success
    (ops RR-KEEP). A caller that needs the edit to have happened reads the
    return value; the others may keep ignoring it.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not (token and chat_id and message_id):
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/editMessageText",
                       json={"chat_id": chat_id, "message_id": message_id, "text": text,
                             "parse_mode": "HTML", "disable_web_page_preview": True},
                       timeout=20)
    except Exception as e:
        log.warning(f"  editMessageText failed: {type(e).__name__}")
        return False
    try:
        ok = r.status_code == 200 and bool(r.json().get("ok"))
    except ValueError:
        ok = False
    if not ok:
        log.warning(f"  editMessageText refused: HTTP {r.status_code} "
                    f"for message {message_id}")
    return ok


def decision_counts(state: dict) -> dict:
    """sent / post / drop / open, and how the posted ones got out.

    `drafts_sent` only started being counted when the keyboard shipped, and
    drafts sent before that were still decided on. Sent can therefore never be
    reported as fewer than decided — otherwise the first Sunday line reads
    "0 gesendet · 1 Verwerfen", which is arithmetic nobody should have to
    explain.

    `post` and `posted` are not the same number and neither is redundant. A
    mention is decided by the button and posted seconds later, so both move
    together. A list draft is decided by Lars opening X, which we never see —
    it only becomes a `post` once the reply shows up on our timeline. The gap
    between the two is drafts handed over and not sent.
    """
    decisions = state.get("decisions", {})
    post = sum(1 for d in decisions.values() if d.get("verb") == "post")
    drop = sum(1 for d in decisions.values() if d.get("verb") == "drop")
    posted = [d for d in decisions.values() if d.get("result") == "posted"]
    decided = post + drop
    sent = max(int(state.get("drafts_sent", 0)), decided)
    return {"sent": sent, "post": post, "drop": drop,
            "open": max(0, sent - decided), "decided": decided,
            "posted": len(posted),
            "posted_manual": sum(1 for d in posted if d.get("route") == "manual"),
            "posted_api": sum(1 for d in posted if d.get("route") != "manual"),
            "handed_over": len(state.get("manual_pending") or {})}


def maybe_report_decisions(state: dict) -> None:
    """One message when the decisions first reach DECISION_REPORT_AT."""
    c = decision_counts(state)
    if c["decided"] < DECISION_REPORT_AT or state.get("decisions_reported"):
        return
    share = (100.0 * c["post"] / c["decided"]) if c["decided"] else 0.0
    # A measurement: daily report, not Telegram (ops NOTIFY-3c).
    from app import tagesbericht_eingang
    tagesbericht_eingang.ablegen(
        "reply_radar",
        f"\U0001f4ca Reply-Radar — {c['decided']} Entscheidungen erreicht\n\n"
        f"Entwürfe gesendet: {c['sent']}\n"
        f"Posten: {c['post']} ({share:.0f} %)\n"
        f"Verwerfen: {c['drop']}\n"
        f"Offen: {c['open']}\n"
        f"Gepostet: {c['posted']} "
        f"({c['posted_manual']} von Hand, {c['posted_api']} per API)\n"
        f"Übergeben, noch nicht gepostet: {c['handed_over']}\n\n"
        f"Erste belastbare Stichprobe: taugen die Entwürfe etwas.")
    state["decisions_reported"] = True


def claim_marks(text: str) -> list[str]:
    """The core claims in a draft, normalised so two spellings meet."""
    seen = {}
    for m in CLAIM_MARK_RE.finditer(text or ""):
        key = re.sub(r"\s+", " ", m.group(0)).strip().lower()
        seen.setdefault(key, None)
    return list(seen)


def claim_history(state: dict, now: datetime.datetime | None = None) -> dict[str, str]:
    """claim -> when a draft last used it, inside the window only.

    Expiring on read matters more than it looks. Pruning happened only in
    remember_claims, which runs when a draft survives — so a spell with no
    drafts kept every claim alive for ever, and the drafter went on being told
    to avoid them. That is a trap that feeds itself: no drafts, no pruning, no
    figures left to write about, no drafts.

    On 28.09 five claims stamped 2026-09-24 02:12 were still being withheld,
    ERC-8004 among them, four days after a 48-hour window had passed.

    The old shape was a bare list. An entry from it is treated as used now:
    the list only ever held the last twelve, all of them recent, and starting
    the window rather than ending it is the conservative reading.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=CLAIM_WINDOW_HOURS)
    raw = state.get("recent_claims")
    if isinstance(raw, dict):
        return {c: t for c, t in raw.items()
                if (_parse_iso(t) or cutoff) > cutoff}
    # The old extractor produced things this one never will — bare years, and
    # fragments with the punctuation still attached ("2026,", "2027."). Each
    # entry is re-read through the current extractor and only what survives is
    # kept, so a dead key does not sit in the window blocking nothing.
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out = {}
    for entry in (raw or []):
        for mark in claim_marks(str(entry)):
            out[mark] = now
    return out


def load_blocklist() -> dict[str, str]:
    try:
        with open(BLOCKLIST_FILE) as f:
            return {str(k).lower(): str(v) for k, v in json.load(f).items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning(f"Cannot read {BLOCKLIST_FILE}: {e}")
        return {}


def claim_conflict(state: dict, text: str, now: datetime.datetime) -> str | None:
    """Why this draft may not go out, or None.

    Two reasons, and both are hard. A claim Lars has blocked by hand, and a
    claim another draft already spent inside the window — once per 48 hours
    across every draft, not once per run. Three replies in one afternoon built
    on the same figure are a bot with one fact, however well each is sourced.
    """
    marks = claim_marks(text)
    if not marks:
        return None
    blocked = load_blocklist()
    for mark in marks:
        until = blocked.get(mark)
        if until and _parse_iso(until) and now < _parse_iso(until):
            return f"“{mark}” is blocked by hand until {until}"
    history = claim_history(state, now)
    cutoff = now - datetime.timedelta(hours=CLAIM_WINDOW_HOURS)
    for mark in marks:
        when = _parse_iso(history.get(mark, ""))
        if when and when > cutoff:
            age = (now - when).total_seconds() / 3600
            return (f"“{mark}” was used {age:.0f} h ago "
                    f"(once per {CLAIM_WINDOW_HOURS} h)")
    return None


def _parse_iso(raw: str) -> datetime.datetime | None:
    try:
        when = datetime.datetime.fromisoformat((raw or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)


def remember_claims(state: dict, text: str) -> None:
    """Record what a draft leant on, with the time, and drop what has expired."""
    now = datetime.datetime.now(datetime.timezone.utc)
    history = claim_history(state)
    cutoff = now - datetime.timedelta(hours=CLAIM_WINDOW_HOURS)
    history = {c: t for c, t in history.items()
               if (_parse_iso(t) or cutoff) > cutoff}
    for mark in claim_marks(text):
        history[mark] = now.isoformat()
    state["recent_claims"] = history


def author_cap_reason(state: dict, tweet: dict, used_this_run: set,
                      today: str) -> str | None:
    """Why this author has had enough for now, or None."""
    if tweet.get("_source") == "mention":
        return None
    author = (tweet.get("_author") or "").lower()
    if not author:
        return None
    if author in used_this_run:
        return f"@{author} already has a draft this run (max {PER_AUTHOR_PER_RUN})"
    done = int((state.get("authors_per_day", {}).get(today, {})).get(author, 0))
    if done >= PER_AUTHOR_PER_DAY:
        return f"@{author} has {done} drafts today (max {PER_AUTHOR_PER_DAY})"
    return None


def count_author(state: dict, tweet: dict, today: str) -> None:
    author = (tweet.get("_author") or "").lower()
    if not author:
        return
    per_day = state.setdefault("authors_per_day", {})
    day = per_day.setdefault(today, {})
    day[author] = int(day.get(author, 0)) + 1
    state["authors_per_day"] = {k: v for k, v in per_day.items() if k >= today}


def count_source(state: dict, tweet: dict, today: str) -> None:
    """One draft, booked to the branch it came from.

    The seven-day question is whether the list or the search produces drafts
    worth posting, and neither the draft counter nor the decisions could answer
    it before: both were blind to where a draft came from.
    """
    by_source = state.setdefault("drafts_by_source", {})
    day = by_source.setdefault(today, {})
    src = tweet.get("_source") or "?"
    day[src] = int(day.get(src, 0)) + 1
    cutoff = (datetime.datetime.strptime(today, "%Y-%m-%d")
              - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    state["drafts_by_source"] = {k: v for k, v in by_source.items() if k >= cutoff}


def count_by_prompt(state: dict, today: str, candidates: int,
                    drafted: int, passed: int) -> None:
    """Candidates, drafts and gate-passes per day per prompt version.

    Live from 2026-10-05, because a draft rate without the prompt that produced
    it is a number nobody can act on — and the 18.10 decision about the reply
    branch will be read against a drafter that changed on the 5th. The rows
    stay separable so that decision is made on the version that was running.
    """
    book = state.setdefault("by_prompt", {})
    day = book.setdefault(today, {})
    row = day.setdefault(PROMPT_VERSION, {"candidates": 0, "drafts": 0,
                                          "gate_pass": 0, "runs": 0})
    row["candidates"] += candidates
    row["drafts"] += drafted
    row["gate_pass"] += passed
    row["runs"] += 1
    cutoff = (datetime.datetime.strptime(today, "%Y-%m-%d")
              - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
    state["by_prompt"] = {k: v for k, v in book.items() if k >= cutoff}


def mark_seen(state: dict, tweet_id: str) -> None:
    """Never consider this post again, whether or not it produced a draft."""
    seen = state.setdefault("seen", [])
    seen.append(tweet_id)
    state["seen"] = seen[-2000:]


def count_draft(state: dict, today: str) -> None:
    per_day = state.setdefault("per_day", {})
    per_day[today] = drafted_today(state, today) + 1
    # Yesterday's counter is dead weight.
    state["per_day"] = {k: v for k, v in per_day.items() if k >= today}


# ── X reading ──

def lookback_since(state: dict, now: datetime.datetime) -> datetime.datetime:
    """The start of the window this run should read.

    Anchored to the last run rather than to a constant, so changing the cadence
    does not silently change what is read twice. Capped, because a week-long
    outage must not make the first run back ask for a week of posts.
    """
    last = state.get("last_run_at")
    try:
        when = datetime.datetime.fromisoformat((last or "").replace("Z", "+00:00"))
    except ValueError:
        when = None
    if when is None:
        return now - datetime.timedelta(hours=LOOKBACK_HOURS)
    if not when.tzinfo:
        when = when.replace(tzinfo=datetime.timezone.utc)
    since = when - datetime.timedelta(minutes=LOOKBACK_OVERLAP_MINUTES)
    floor = now - datetime.timedelta(hours=LOOKBACK_MAX_HOURS)
    return max(since, floor)


def load_targets() -> dict:
    """handle (lowercased) -> {group, tier, followers}. Empty when unreadable."""
    try:
        with open(TARGETS_FILE) as f:
            data = json.load(f).get("targets", {})
        return {k.lower(): v for k, v in data.items()}
    except Exception as e:
        log.warning(f"Could not read {TARGETS_FILE}: {e}")
        return {}


def tier_of(tweet: dict, targets: dict) -> int:
    """1 is answered first, 4 only when the post is on our subject.

    A big account is tier 4 whatever its group says: @VitalikButerin posting
    about anything at all is not an opening, and @VitalikButerin posting about
    ERC-8004 is.
    """
    entry = targets.get((tweet.get("_author") or "").lower())
    if not entry:
        return 3 if tweet.get("_source") == "mention" else 4
    if (entry.get("followers") or 0) > BIG_ACCOUNT_FOLLOWERS:
        return 4
    return int(entry.get("tier", 4))


def x_auth() -> OAuth1 | None:
    keys = [os.getenv(k, "") for k in
            ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")]
    return OAuth1(*keys) if all(keys) else None


# CM3: Senden posts from Lars's own account and from no other. The consumer pair
# is the existing app's; the access pair is his, under names of its own, written
# by ~/Downloads/x-lars-auth.sh. There is deliberately no fallback to
# X_ACCESS_TOKEN: that pair is @MolTrust, whose reply branch ended on 18.10
# (BW1), and a missing unlock has to be answered, not routed around.
LARS_TOKEN_VARS = ("X_CONSUMER_KEY", "X_CONSUMER_SECRET",
                   "X_LARS_ACCESS_TOKEN", "X_LARS_ACCESS_SECRET")
LARS_HANDLE = "LarsOnMoltrust"


def lars_auth() -> OAuth1 | None:
    keys = [os.getenv(k, "") for k in LARS_TOKEN_VARS]
    return OAuth1(*keys) if all(keys) else None


def copy_block(text: str, tweet_id: str) -> str:
    """The draft to copy and the post it answers, for every refusal on Senden."""
    return (f"Zum Kopieren:\n<pre>{html.escape(text)}</pre>\n"
            f"Zielpost: https://x.com/i/status/{urllib.parse.quote(tweet_id)}")


FIELDS = "created_at,public_metrics,author_id,conversation_id,lang,referenced_tweets"
USER_FIELDS = "username,name,public_metrics"


# Collected during a run and reported once at the end, so five broken searches
# are one message rather than five.
_SOURCE_FAILURES: list[tuple[str, int, str]] = []


def note_source_failure(url: str, status: int, body: str) -> None:
    _SOURCE_FAILURES.append((url.rsplit("/", 1)[-1].split("?")[0], status, body))


def report_source_failures(state: dict) -> None:
    """Tell STATS when a source is broken, once a day per shape of failure.

    A 400 that repeats every four hours does not need four messages; a 400 that
    is still there tomorrow does.
    """
    if not _SOURCE_FAILURES:
        return
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    seen = state.setdefault("source_failures_reported", {})
    fresh = [f for f in _SOURCE_FAILURES if seen.get(f"{f[0]}:{f[1]}") != today]
    for f in _SOURCE_FAILURES:
        seen[f"{f[0]}:{f[1]}"] = today
    state["source_failures_reported"] = {k: v for k, v in seen.items() if v >= today}
    if not fresh:
        return
    body = "\n\n".join(f"<b>{html.escape(name)}</b> → HTTP {status}\n"
                         f"<pre>{html.escape(detail[:220])}</pre>"
                         for name, status, detail in fresh)
    notify.send_telegram(
        f"\u26a0\ufe0f Reply-Radar: Quelle antwortet nicht\n\n{body}\n\n"
        f"Ein Lauf mit einer kaputten Quelle meldet null Kandidaten und sieht "
        f"aus wie ein ruhiger Tag.", channel=notify.ALERTS)


def report_reads_paused(state: dict, reason: str) -> None:
    """A run that read nothing because the breaker was closed says so, once."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    if state.get("paused_reported_on") == today:
        return
    state["paused_reported_on"] = today
    notify.send_telegram(
        f"\u23f8 Reply-Radar übersprungen\n\n{reason}\n\n"
        f"Die Läufe bis 00:00 UTC lesen nichts. Posten ist nicht betroffen.",
        channel=notify.STATS)


def _get(auth, url: str, params: dict) -> dict:
    paused = x_meter.reads_paused()
    if paused:
        log.warning(f"  skipped {url.rsplit('/', 1)[-1]}: {paused}")
        return {}
    try:
        # X bills per resource returned, not per call; x_meter.get books what
        # came back rather than that we called.
        r = x_meter.get(url, params=params, auth=auth, timeout=30,
                        source=url.rsplit("/", 1)[-1].split("?")[0],
                        origin=x_meter.ORIGIN_RADAR)
    except Exception as e:
        log.error(f"GET {url} failed: {e}")
        return {}
    if r.status_code != 200:
        log.warning(f"GET {url} -> {r.status_code}: {r.text[:180]}")
        # A source that answers 4xx is broken, not empty. The list returned 400
        # for 24 hours while every run reported "0 candidates" and looked fine.
        if 400 <= r.status_code < 500 and r.status_code != 429:
            note_source_failure(url, r.status_code, r.text[:300])
        return {}
    return r.json()


def _author_expansion() -> dict:
    """The author expansion, or nothing once today's profile budget is spent.

    A profile costs twice a post, and the expansion is where they come from —
    X bills per resource returned, so asking for authors is what buys them. The
    cap is deliberately not a second breaker: the search still runs and the
    posts still arrive, the radar just stops learning who wrote them for the
    rest of the UTC day. Scoring that needs a follower count skips those
    candidates rather than guessing one.
    """
    why = x_meter.profiles_exhausted()
    if why:
        log.info(f"  {why}")
        return {}
    return {"expansions": "author_id", "user.fields": USER_FIELDS}


def gather(auth, since: datetime.datetime, since_id: str | None = None,
           include_search: bool = True, targets: dict | None = None) -> list[dict]:
    """Candidate posts from all three sources, newest first, deduped by id.

    `since_id` applies to the list only. Search and mentions are already bounded
    by start_time; the list has no time filter of its own, so without this it
    returns the same page every run and we pay to re-read what we filtered out
    two hours ago.
    """
    start = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict[str, dict] = {}
    authors: dict[str, dict] = {}

    def absorb(body: dict, source: str):
        for u in body.get("includes", {}).get("users", []):
            authors[u["id"]] = u
        for t in body.get("data", []) or []:
            t["_source"] = source
            out.setdefault(t["id"], t)

    # No author expansion on the list. Every member is in reply_targets.json
    # with their handle and follower count, and a profile read costs $0.010 —
    # twice what the post itself costs. Half of a run's bill was profiles we
    # already had on disk.
    absorb(_get(auth, f"https://api.twitter.com/2/lists/{TARGETS_LIST_ID}/tweets",
                {"max_results": list_page(), "tweet.fields": FIELDS}), "list")

    # Search and mentions do need it: a stranger's follower count decides
    # whether we answer at all, and it is nowhere on disk.
    if include_search:
        for q in SEARCH_QUERIES:
            absorb(_get(auth, "https://api.twitter.com/2/tweets/search/recent",
                        {"query": q, "max_results": SEARCH_PAGE,
                         "start_time": start, "tweet.fields": FIELDS,
                         **_author_expansion()}), "search")
    absorb(_get(auth, f"https://api.twitter.com/2/users/{OUR_USER_ID}/mentions",
                {"max_results": 25, "start_time": start, "tweet.fields": FIELDS,
                 **_author_expansion()}), "mention")

    by_id = {str(v.get("user_id")): (h, v) for h, v in (targets or {}).items()
             if v.get("user_id")}
    for t in out.values():
        u = authors.get(t.get("author_id"), {})
        handle, cfg = by_id.get(str(t.get("author_id")), (None, {}))
        # The expansion when we paid for one, the config when we did not.
        t["_author"] = u.get("username") or handle or "?"
        t["_author_followers"] = ((u.get("public_metrics") or {}).get("followers_count")
                                  if u else cfg.get("followers"))
    return list(out.values())


def newest_list_id(items: list[dict]) -> str | None:
    """The highest id seen on the list this run. X ids sort as integers."""
    ids = [t["id"] for t in items if t.get("_source") == "list" and t.get("id")]
    return max(ids, key=int) if ids else None


def worth_answering(t: dict, state: dict, since: datetime.datetime,
                    targets: dict) -> bool:
    return drop_reason(t, state, since, targets) is None


def drop_reason(t: dict, state: dict, since: datetime.datetime,
                targets: dict) -> str | None:
    """Why this post is not a candidate, or None.

    The reason is the point. "86 filtered" answers nothing; the radar has to be
    able to say how many of those were dropped by the tier-4 topic rule, which
    is the only filter here that turns away a post a person might have wanted
    answered.
    """
    if t["id"] in set(state.get("seen", [])):
        return "seen"
    if t.get("author_id") == OUR_USER_ID:
        return "ours"
    if any(r.get("type") == "retweeted" for r in t.get("referenced_tweets") or []):
        return "retweet"
    if t.get("lang") not in (None, "en"):
        return f"lang {t.get('lang')}"
    created = t.get("created_at")
    if created:
        when = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
        if when < since:
            return "older than the lookback"
    # A post with nothing in it gives a reply nothing to hold on to.
    if len((t.get("text") or "").split()) < 8:
        return "under eight words"

    text = t.get("text") or ""
    if CASHTAG_RE.search(text):
        return "cashtag"
    if THREAD_COUNTER_RE.search(text):
        return "thread counter"

    # The curated list has already answered "is this worth watching". Applying
    # a numeric floor on top would be curating twice, and the accounts that
    # matter most to us are the small ones.
    if t.get("_source") != "list":
        impressions = (t.get("public_metrics") or {}).get("impression_count", 0)
        if impressions < MIN_IMPRESSIONS:
            return f"impressions {impressions} < {MIN_IMPRESSIONS}"
        followers = t.get("_author_followers")
        if followers is not None and followers < MIN_AUTHOR_FOLLOWERS:
            return f"followers {followers} < {MIN_AUTHOR_FOLLOWERS}"

    # Tier 4 — prediction markets, and anyone over a million followers — only
    # when the post is about the thing we have something to say about.
    if tier_of(t, targets) >= 4 and not ON_TOPIC_RE.search(text):
        return "tier 4, off topic"
    return None


TIER4_SAMPLE = 10


def filter_report(auth, state: dict, targets: dict, send: bool) -> dict:
    """What the filters turned away this run, and a sample of the worst case.

    The tier-4 topic rule is the only filter here that refuses a post a person
    might have wanted answered — everything else drops retweets, noise, or
    posts nobody saw. So it gets named and sampled, and the sample goes to
    Telegram where Lars can say whether the rule is too tight.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    since = lookback_since(state, now)
    tally: dict[str, int] = {}
    tier4: list[dict] = []
    kept = 0
    for t in gather(auth, since, since_id=state.get("list_since_id"),
                    targets=targets):
        why = drop_reason(t, state, since, targets)
        if why is None:
            kept += 1
            continue
        tally[why] = tally.get(why, 0) + 1
        if why == "tier 4, off topic" and len(tier4) < TIER4_SAMPLE:
            tier4.append(t)

    log.info(f"Filter report: {kept} kept, " +
             ", ".join(f"{n} {k}" for k, n in sorted(tally.items(), key=lambda kv: -kv[1])))
    if not send:
        return {"kept": kept, "tally": tally, "sample": tier4}

    lines = []
    for t in tier4:
        metrics = t.get("public_metrics") or {}
        lines.append(
            f"· @{t.get('_author')} ({metrics.get('impression_count', 0)} Impr)\n"
            f"  <i>{html.escape((t.get('text') or '')[:170])}</i>\n"
            f"  https://x.com/{t.get('_author', 'i')}/status/{t['id']}")
    hours = (now - since).total_seconds() / 3600
    body = (f"\U0001f50d Radar-Filter, letzte {hours:.1f} h\n\n"
            f"Kandidaten: {kept}\n"
            + "\n".join(f"{n}× {html.escape(k)}" for k, n in
                         sorted(tally.items(), key=lambda kv: -kv[1]))
            + f"\n\nTier 4, off topic — Stichprobe {len(tier4)} von "
              f"{tally.get('tier 4, off topic', 0)}:\n\n" + "\n\n".join(lines)
            + "\n\nDie Regel antwortet nur, wenn ein Tier-4-Post Agent-Identität, "
              "x402 oder ERC-8004 nennt. Wenn davon etwas beantwortbar aussieht, "
              "ist sie zu eng.")
    # A measurement: daily report, not Telegram (ops NOTIFY-3c).
    from app import tagesbericht_eingang
    tagesbericht_eingang.ablegen("reply_radar", body, html=True)
    return {"kept": kept, "tally": tally, "sample": tier4}


def rank(items: list[dict], targets: dict) -> list[dict]:
    """Tier first, then the list over search, then how many people saw it."""
    source_order = {"list": 0, "mention": 1, "search": 2}
    return sorted(items, key=lambda t: (
        tier_of(t, targets),
        source_order.get(t.get("_source"), 3),
        -(t.get("public_metrics") or {}).get("impression_count", 0)))


# ── Drafting ──

def load_anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            with open(os.path.expanduser("~/.anthropic_key")) as f:
                key = f.read().strip()
        except Exception:
            pass
    return key


MAX_SOURCES = 4
MAX_SOURCE_BYTES = 400_000

# Our own published pages. The drafter cannot search, so without these it can
# only cite URLs it remembers — and a remembered URL is exactly what (h) exists
# to stop us trusting. These are fetched once per run and offered to it, which
# is the "KB" half of the rule: our own figures, already published, checkable
# by the person reading the reply.
KB_PAGES = [
    "https://moltrust.ch/blog/feed.xml",
    "https://moltrust.ch/integrity.html",
    "https://moltrust.ch/developers.html",
    "https://moltrust.ch/skills.html",
    "https://moltrust.ch/whitepaper.html",
]
KB_RECENT_POSTS = 6          # newest blog entries, read out of the feed
KB_EXCERPT_CHARS = 1200      # what the drafter sees per page in the prompt


def post_links(tweet: dict) -> list[str]:
    """http(s) links in the post being answered. Often the report it cites."""
    return re.findall(r"https?://[^\s]+", tweet.get("text") or "")[:2]


def fetch_sources(urls: list[str]) -> dict[str, str]:
    """Fetch what the draft cited. Only http(s), only a handful, bounded.

    Rule (h) compares the draft against these texts, so a URL that does not
    answer contributes nothing and the claim that leant on it will block.
    """
    out: dict[str, str] = {}
    for url in urls[:MAX_SOURCES]:
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            log.warning(f"  ignoring non-http source: {str(url)[:60]}")
            continue
        try:
            r = httpx.get(url, timeout=25, follow_redirects=True,
                          headers={"User-Agent": "MolTrust-ReplyRadar/0.1 (+https://moltrust.ch)"})
        except Exception as e:
            log.warning(f"  source fetch failed {url}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            log.warning(f"  source {url} -> HTTP {r.status_code}")
            continue
        out[url] = strip_html(r.text[:MAX_SOURCE_BYTES])
        log.info(f"  fetched {url} ({len(out[url])} chars)")
    return out


def strip_html(body: str) -> str:
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", body)))


def fetch_one(url: str) -> str | None:
    try:
        r = httpx.get(url, timeout=25, follow_redirects=True,
                      headers={"User-Agent": "MolTrust-ReplyRadar/0.1 (+https://moltrust.ch)"})
    except Exception as e:
        log.warning(f"  fetch failed {url}: {type(e).__name__}")
        return None
    if r.status_code != 200:
        log.warning(f"  {url} -> HTTP {r.status_code}")
        return None
    return strip_html(r.text[:MAX_SOURCE_BYTES])


def load_kb() -> dict[str, str]:
    """Our own pages, fetched once per run. The feed supplies the newest posts."""
    kb: dict[str, str] = {}
    for url in KB_PAGES:
        text = fetch_one(url)
        if text:
            kb[url] = text
    feed = kb.get("https://moltrust.ch/blog/feed.xml", "")
    for link in re.findall(r"https://moltrust\.ch/blog/[\w-]+\.html", feed)[:KB_RECENT_POSTS]:
        if link in kb:
            continue
        text = fetch_one(link)
        if text:
            kb[link] = text
    log.info(f"KB: {len(kb)} pages, {sum(len(v) for v in kb.values())} chars")
    return kb


def kb_digest(kb: dict[str, str]) -> str:
    """What the drafter is shown: each page's URL and the head of its text."""
    out = []
    for url, text in kb.items():
        out.append(f"--- {url}\n{text[:KB_EXCERPT_CHARS]}")
    return "\n\n".join(out)


# The prompt is versioned from 2026-10-05, because a draft rate without the
# prompt that produced it is a number nobody can act on. Recorded with every
# draft and reported daily, split by version.
#
# Measured over the same 49 refused candidates, all three prompts, identical
# inputs and the gate given the sources production gives it:
#
#   alt  9 drafts,  3 gate-pass, 6 blocked   (5 of them g2f: no figure at all)
#   neu 13 drafts, 13 gate-pass, 0 blocked
#   v3  14 drafts, 11 gate-pass, 3 blocked   (all three gate 1 prose faults)
#
# v3 ships although `neu` counted two higher, on instruction and for a reason
# worth writing down: neither produced a single (h) block, but v3 grounds every
# figure in the citation index rather than in whatever the fetch happened to
# supply. Eleven passes that can name their page beat thirteen where the
# grounding was partly luck. Its three blocks are prose, not sourcing — the
# index hands it more figures and the sentences get ornate.
# 2026-10-11: SKIP carries a reason code. Same rules, one more line of output;
# the refusals were the one part of the funnel nobody could read. Still v3, so
# deploy_verify keeps checking the v3 markers.
PROMPT_VERSION = "v3-2026-10-11"

VARIANT3_RULE = """

**What to refuse, and what to answer.**

Return SKIP for:
- advertising and product promotion, including our own kind of product
- shill posts, mint promotion, price talk, airdrop and allowlist posts
- anything carrying no claim a reader could check

Draft a reply when the post carries a **technical claim about identity,
ownership, signature, authorization or provability** — even if the word "agent"
never appears. A claim about a handle minted on-chain, a wallet proving
ownership with a short-lived signature, an access check that trusts the
caller's own assertion: all of these are the subject, whatever vocabulary the
post uses. The test is the claim, not the topic word.

**The source rule — this one decides whether you may write at all.**

Answer only if **the post you are answering itself states a checkable number or
claim**, or **the citation index below carries one on this subject**. A
remembered paragraph number, a remembered date and a remembered figure are not
sources. "EU AI Act Article 12" from memory is not a citation; the same string
read off a page in the index, with that page's URL, is.

No hit in the index and no figure in the post: **SKIP**. That is the common
case and it is the right answer.

**Product announcements and advertising stay SKIP even when they carry a
number.** A launch post with a version number, a funding figure or a user count
is still promotion, and a reply to it reads as one more voice in somebody's
campaign.
"""


# Built once per process. A run asks for it per candidate, and each build reads
# the feed plus some seventy HTML files off disk — three candidates meant three
# full passes over the blog. The content cannot change inside a run: the blog is
# deployed between runs, not during one.
_INDEX_CACHE: str | None = None
_MODULE_CACHE = None


def _citation_module():
    """The index module, loaded once per process.

    Shared with citation_block() so the exclusion list the gate checks against
    is the one that built the index, not a second load that could disagree.
    """
    global _MODULE_CACHE
    if _MODULE_CACHE is not None:
        return _MODULE_CACHE or None
    import importlib.util
    import sys as _sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "scripts", "citation_index.py")
    spec = importlib.util.spec_from_file_location("citation_index", path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["citation_index"] = mod
    spec.loader.exec_module(mod)
    _MODULE_CACHE = mod
    return mod


def citation_block() -> str:
    """The index, or nothing — never a half one.

    An index that failed to build would leave the source rule pointing at an
    empty list, which reads to the model as "nothing is citable" and turns
    every candidate into a SKIP. Better to fall back to the prompt without the
    rule than to ship a rule with no index behind it.
    """
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    try:
        mod = _citation_module()
        idx = mod.build()
        if not idx["posts"]:
            log.warning("citation index empty — drafting without the source rule")
            _INDEX_CACHE = ""
            return ""
        log.info(f"Citation index: {idx['counts']['posts']} posts, "
                 f"{idx['counts']['figures']} figures")
        _INDEX_CACHE = mod.as_prompt(idx)
        return _INDEX_CACHE
    except Exception as e:
        # Cached as empty too: a run whose first attempt failed should not
        # retry the same failing read once per candidate, and the fallback is
        # the same either way.
        log.warning(f"citation index unavailable ({type(e).__name__}) — "
                    f"drafting without the source rule")
        _INDEX_CACHE = ""
        return ""


# Room for adaptive thinking plus a 275-character reply and its JSON. Billed by
# what is used, so the ceiling costs nothing until a draft needs it.
DRAFT_MAX_TOKENS = 8000

# Why the drafter returned nothing, per candidate (BN1, 11.10.2026). Before
# this every refusal was one log line, "nothing factual to say", and 687 of 809
# candidates in the 14 days to 03.10 ended there — the largest step in the
# funnel, and the only one without a reason.
SKIP_CODES = {"promotion", "price-talk", "no-checkable-claim", "no-source",
              "off-subject", "repeat-figure", "other"}
SKIP_RE = re.compile(r"SKIP\s*:\s*([a-z-]+)", re.I)
REFUSALS_FILE = os.path.join(DATA_DIR, "reply_radar_refusals.jsonl")

# Set by draft_reply for the candidate it just refused; read by run().
# kind is a SKIP_CODES entry or "uncoded" for a refusal by the model, else
# truncated / empty / unparseable / api-error / no-key.
_LAST_REFUSAL: dict = {}


def _refused(kind: str, detail: str, skip: bool = False) -> None:
    _LAST_REFUSAL.clear()
    _LAST_REFUSAL.update({"kind": kind, "detail": detail, "skip": skip})


def record_refusal(state: dict, tweet: dict, today: str, targets: dict) -> str:
    """One line per refused candidate, and a per-day count by kind. Returns
    the log text. Best-effort on the file: a full disk must not stop the run."""
    r = dict(_LAST_REFUSAL) or {"kind": "unknown", "detail": "", "skip": False}
    _LAST_REFUSAL.clear()
    row = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "tweet_id": tweet.get("id"), "author": tweet.get("_author"),
           "source": tweet.get("_source"), "tier": tier_of(tweet, targets),
           "prompt": PROMPT_VERSION, **r}
    try:
        with open(REFUSALS_FILE, "a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning(f"  refusal log write failed: {type(e).__name__}")
    day = state.setdefault("refusals", {}).setdefault(today, {})
    day[r["kind"]] = day.get(r["kind"], 0) + 1
    cutoff = (datetime.datetime.strptime(today, "%Y-%m-%d")
              - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
    state["refusals"] = {k: v for k, v in state["refusals"].items() if k >= cutoff}
    return f"{r['kind']}: {r['detail'][:200]}"


def _refusal_is_final(why: str) -> bool:
    """A refusal by the model marks the post seen. A cut-off, an empty answer
    or an API failure is ours, not the post's: the next run asks again."""
    return why.split(":", 1)[0] not in {"truncated", "api-error", "no-key", "empty",
                                        "retired"}


def draft_reply(tweet: dict, kb: dict[str, str],
                avoid: list[str] | None = None) -> tuple[str, list[str]] | None:
    _LAST_REFUSAL.clear()
    # Before the key is even read: 18.10 to 26.10 no candidate reaches the
    # model. From 27.10 it does again (CM13); posting stays shut either way.
    if drafter_retired():
        log.info(f"  drafter not called — {RETIRED_REASON}")
        _refused("retired", RETIRED_REASON)
        return None
    key = load_anthropic_key()
    if not key:
        log.error("No Anthropic API key available")
        _refused("no-key", "no Anthropic API key")
        return None
    docs = voice_gate.load_voice_docs()
    index = citation_block()
    system = (SYSTEM_PROMPT
              + (VARIANT3_RULE if index else "")
              + "\n\n=== anti-KI-Sprech.md (negative list) ===\n" + docs["anti_ki_sprech"]
              + "\n\n=== my-voice-en.md (positive model) ===\n" + docs["my_voice_en"]
              + "\n\n=== our published pages, already fetched — cite these by URL ===\n"
              + kb_digest(kb)
              + ("\n\n" + index if index else ""))
    user = f"Post by @{tweet.get('_author', '?')}:\n\n{tweet.get('text', '')}\n\n"
    if avoid:
        user += ("Figures used in recent replies — reach for something else, or SKIP "
                 "if this post only supports one of these:\n"
                 + ", ".join(avoid) + "\n\n")
    user += "Write the reply, or SKIP."
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages",
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                       # The system block is byte-identical for every
                       # candidate in a run and the citation index is some
                       # 8 000 tokens of it, so it is marked cacheable: one run
                       # pays for it once instead of once per candidate.
                       # Thinking stated, not defaulted. Opus 5 thinks
                       # adaptively when the field is absent, and thinking
                       # tokens count against max_tokens: at 1500 a long think
                       # could end the answer before it began, and an empty
                       # answer read as SKIP. DRAFT_MAX_TOKENS leaves room for
                       # both; stop_reason below tells a cut-off from a refusal.
                       json={"model": MODEL, "max_tokens": DRAFT_MAX_TOKENS,
                             "thinking": {"type": "adaptive"},
                             "system": [{"type": "text", "text": system,
                                         "cache_control": {"type": "ephemeral"}}],
                             "messages": [{"role": "user", "content": user}]},
                       timeout=180)
    except Exception as e:
        log.error(f"Claude call failed: {e}")
        _refused("api-error", f"{type(e).__name__}")
        return None
    if r.status_code != 200:
        log.error(f"Claude API {r.status_code}: {r.text[:200]}")
        _refused("api-error", f"HTTP {r.status_code}")
        return None
    body = r.json()
    stop = body.get("stop_reason")
    # Tokens per call, so a run's model cost is read from the log and not
    # estimated (11.10.2026, BO7: the radar booked X reads but no tokens).
    u = body.get("usage") or {}
    log.info(f"  tokens in={u.get('input_tokens')} out={u.get('output_tokens')} "
             f"cache_read={u.get('cache_read_input_tokens')} "
             f"cache_write={u.get('cache_creation_input_tokens')} stop={stop}")
    blocks = body.get("content", [])
    raw = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if stop == "max_tokens":
        out_tok = (body.get("usage") or {}).get("output_tokens")
        _refused("truncated", f"stop_reason max_tokens after {out_tok} output tokens")
        return None
    if not raw:
        _refused("empty", f"no text, stop_reason {stop}")
        return None
    if raw.upper().startswith("SKIP"):
        m = SKIP_RE.match(raw)
        code = m.group(1).lower() if m and m.group(1).lower() in SKIP_CODES else "uncoded"
        _refused(code, raw[:300], skip=True)
        return None
    try:
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
    except Exception as e:
        log.warning(f"  draft JSON unparseable ({e}); treating as a skip")
        _refused("unparseable", raw[:300])
        return None
    text = str(data.get("reply", "")).strip().strip('"')
    sources = [s for s in (data.get("sources") or []) if isinstance(s, str)]
    if not text:
        _refused("empty", "JSON without reply text")
        return None
    # CM8: the sources belong under the draft, not in it. A draft that names
    # its page inside the 280 characters is refused rather than cleaned —
    # stripping the URL leaves a sentence that was written around it ("more
    # here: …") and nobody sees that the text changed after the gates ran.
    carried = x_meter.link_in(text)
    if carried:
        _refused("link", f"reply carries a link ({carried})")
        return None
    return text, sources


def check(text: str, sources: dict[str, str]) -> tuple[bool, list[str], dict]:
    """Both gates in reply mode, plus the no-pitch rule.

    `sources` is what rule (h) grounds the claims against: the pages the draft
    named, as fetched in this run.
    """
    scan = voice_gate.scan([text], mode="reply", sources=sources)
    problems = list(scan["violations"])
    hit = PRODUCT_RE.search(text)
    if hit:
        problems.append(f"no-pitch: names our own product ({hit.group(0)})")
    # A source the index refused must not come back in through the draft. The
    # index not offering it is not enough: the drafter also carries the post it
    # answers and whatever it recalls, and candidate material reads exactly
    # like a pinned fact once it is in a sentence.
    for ident, why in _citation_module_excluded(text):
        problems.append(f"source-rule: cites {ident} — {why}")
    return (not problems), problems, scan


def _citation_module_excluded(text: str) -> list[tuple[str, str]]:
    """cites_excluded() from the index module, or nothing if it cannot load.

    Never raises: a drafter that cannot check is a drafter that still has to
    run, and the gate above already blocks an ungrounded claim. A failure to
    load is logged, because a guard that silently stops guarding is the shape
    we keep finding.
    """
    try:
        mod = _citation_module()
        return mod.cites_excluded(text) if mod else []
    except Exception as e:  # noqa: BLE001
        log.warning(f"source rule not checked: {type(e).__name__}: {e}")
        return []


# ── Output ──

def draft_id(tweet_id: str, verb: str) -> str:
    """callback_data for the keyboard. Telegram caps it at 64 bytes."""
    return f"rr|{verb}|{tweet_id}"[:64]


def intent_link(tweet_id: str, text: str) -> str:
    """A prepared reply, opened in X, posted by a person.

    The only route left for a third-party post. The text arrives filled in, so
    what goes out is still the draft that passed the gates — it just travels
    through a browser instead of through our credentials.

    Refused 18.10 to 30.10: a prepared reply is a post one tap away, and the
    retirement covered that route as much as the API. Open again from 31.10
    (CM16), because the person who taps it is the one who posts.
    """
    if reply_branch_retired() and not intent_link_open():
        raise RuntimeError(f"no intent link — {RETIRED_REASON}")
    return (f"{INTENT_URL}?in_reply_to={urllib.parse.quote(tweet_id)}"
            f"&text={urllib.parse.quote(text)}")


def decision_keys(tweet_id: str) -> dict:
    """CM2: two keys, Senden and Verwerfen, and no third one anywhere.

    There were three shapes before — Posten plus Verwerfen for a mention, an
    intent link plus Verwerfen for a list post, nothing for a blocked draft —
    and each one needed its own sentence in the message to explain which key
    did what. One keyboard means one explanation: Senden tries to send, and
    what happens when the API refuses is answered on the press, where the
    answer can carry the text to copy.
    """
    return {"inline_keyboard": [[
        {"text": "✅ Senden", "callback_data": draft_id(tweet_id, "post")},
        {"text": "\U0001f5d1 Verwerfen", "callback_data": draft_id(tweet_id, "drop")},
    ]]}


def draft_expired(msg: dict, now: datetime.datetime | None = None) -> bool:
    """Has the message carrying this draft passed DRAFT_TTL_HOURS?

    The clock is Telegram's own `date` on the message the key sits under, not a
    note of ours: a press arrives with the message it belongs to, so this
    survives a lost state file and answers the same way after a restart.

    A message without a readable date counts as expired. The permissive side of
    this branch posts a reply of unknown age, and that is the one outcome the
    job rules out.
    """
    stamp = msg.get("date")
    try:
        offered = datetime.datetime.fromtimestamp(int(stamp),
                                                  datetime.timezone.utc)
    except (TypeError, ValueError):
        return True
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - offered) > datetime.timedelta(hours=DRAFT_TTL_HOURS)


def send_draft(idx: int, tweet: dict, text: str, ok: bool,
               problems: list[str], sources: dict[str, str]) -> int | None:
    if drafter_retired():
        log.warning(f"  draft for {tweet.get('id')} not sent — {RETIRED_REASON}")
        return None
    source = tweet.get("_source")
    api_replyable = source in API_REPLYABLE_SOURCES
    url = f"https://x.com/{tweet.get('_author', 'i')}/status/{tweet['id']}"
    metrics = tweet.get("public_metrics") or {}
    head = "\U0001f4dd Reply-Entwurf" if ok else "\u26a0\ufe0f Reply-Entwurf BLOCKIERT"
    body = (f"{head} {idx}\n"
            f"Quelle ({source}): {url}\n"
            f"Autor: @{html.escape(str(tweet.get('_author') or 'unbekannt'))}\n"
            f"{metrics.get('impression_count', 0)} Impressionen · "
            f"{metrics.get('like_count', 0)} Likes\n\n"
            f"<pre>{html.escape((tweet.get('text') or '')[:400])}</pre>\n\n"
            f"Entwurf ({len(text)}/280):\n<pre>{html.escape(text)}</pre>\n\n")
    if sources:
        body += ("Belege (im Lauf geholt, Gate 2 (h) geprüft):\n"
                 + "\n".join(f"· {html.escape(u)}" for u in sources) + "\n\n")
    # CM2: the gate result belongs in the message in both directions. A message
    # that shows findings only when there are findings leaves the reader
    # guessing whether the gate ran at all.
    if problems:
        body += ("Voice-Gate: Befunde\n<pre>"
                 + html.escape("\n".join(problems)[:900]) + "</pre>\n\n")
    else:
        body += "Voice-Gate: bestanden\n\n"

    markup = None
    if reply_branch_retired() and ok and intent_link_open():
        # CM16/CM17/CM21: the draft as text to read, the link underneath as
        # text, no key. Ignoring the message is the discard.
        link = intent_link(tweet["id"], text)
        body += (f'<a href="{html.escape(link)}">Antwort auf X öffnen</a>\n'
                 "Der Link öffnet X mit Zielpost und Entwurf; gepostet wird "
                 "von dem Account, der dort angemeldet ist. Nach einem "
                 "Kontowechsel den Link erneut antippen, Zielpost und Text "
                 "sind dann wieder gesetzt.\n"
                 "Verwerfen heißt ignorieren.")
    elif reply_branch_retired():
        # CM14: the draft travels as text, nothing more. No post button, no
        # intent link, no callback — there is no key in this message that
        # leads to X. Lars copies the text and answers from his own account.
        body += ("Antwort von Hand: Text oben markieren und kopieren, der "
                 "Zielpost ist oben verlinkt. Dieser Entwurf trägt keinen "
                 "Knopf — es gibt keinen Weg von hier auf X.")
    elif ok:
        markup = decision_keys(tweet["id"])
        if api_replyable:
            body += ("Erwähnung — hier darf die API antworten. Senden postet "
                     "innerhalb von fünf Minuten, nach erneuter Gate-Prüfung "
                     "gegen frisch geholte Belege.\n")
        else:
            body += ("Fremder Post — die X-API antwortet darauf auf keinem Tier "
                     "(403). Senden sagt das und gibt den Text zum Kopieren "
                     "zurück.\n")
        body += (f"Der Entwurf verfällt {DRAFT_TTL_HOURS} Stunden nach dieser "
                 f"Meldung; ein späterer Druck auf Senden sendet nichts.")
    else:
        body += "Nichts wird gepostet."
    return notify.send_telegram_message(body, channel=notify.STATS, parse_mode="HTML",
                                        reply_markup=markup)


def remember_manual(state: dict, tweet: dict, text: str, message_id) -> None:
    """A draft handed over for manual posting, so the detector knows to look."""
    state.setdefault("manual_pending", {})[tweet["id"]] = {
        "text": text,
        "author": tweet.get("_author"),
        "source": tweet.get("_source"),
        "message_id": message_id,
        "chat_id": notify.chat_id_for(notify.STATS),
        "offered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def followers_now(auth) -> int | None:
    """The follower count right now, or None. A delta needs something to
    subtract from; a failure to read it must not cost the post."""
    try:
        r = x_meter.get("https://api.twitter.com/2/users/me",
                        params={"user.fields": "public_metrics"}, auth=auth, timeout=20,
                        source="followers", origin=x_meter.ORIGIN_RADAR)
        if r.status_code == 200:
            return r.json()["data"]["public_metrics"]["followers_count"]
    except Exception as e:
        log.warning(f"  follower baseline unavailable: {type(e).__name__}")
    return None


def detect_manual_posts(state: dict, auth) -> int:
    """Find replies posted by hand and close the loop on them.

    Our own timeline is the only place this is visible. A reply we did not send
    through the API still appears there, with the target in `referenced_tweets`,
    so matching that against the drafts we handed over proves the posting
    without anyone having to confirm it.
    """
    pending = state.get("manual_pending") or {}
    if not pending:
        return 0
    body = _get(auth, f"https://api.twitter.com/2/users/{OUR_USER_ID}/tweets",
                {"max_results": 25, "tweet.fields": "referenced_tweets,created_at"})
    found = 0
    for t in body.get("data", []) or []:
        for ref in t.get("referenced_tweets") or []:
            target = ref.get("id")
            if ref.get("type") != "replied_to" or target not in pending:
                continue
            entry = pending.pop(target)
            link = f"https://x.com/MolTrust/status/{t['id']}"
            state.setdefault("decisions", {})[target] = {
                "verb": "post", "route": "manual", "result": "posted",
                "source": entry.get("source"),
                "reply_id": t["id"], "link": link,
                "posted_at": t.get("created_at"),
                "followers_at_post": followers_now(auth),
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            per_day = state.setdefault("posted_per_day", {})
            per_day[today] = int(per_day.get(today, 0)) + 1
            edit_message(entry.get("chat_id"), entry.get("message_id"),
                         f"\u2705 Gepostet\n{link}\n\n"
                         f"<pre>{html.escape(entry.get('text', ''))}</pre>")
            log.info(f"  manual reply detected for {target}: {link}")
            reply_decisions.close(target, reply_decisions.SENT)
            found += 1
    # Stop looking for an offer nobody took. The draft stays in the chat; only
    # the watching ends, so the timeline read does not grow without bound.
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=MANUAL_PENDING_HOURS)
    for target, entry in list(pending.items()):
        offered = entry.get("offered_at")
        try:
            stale = offered and datetime.datetime.fromisoformat(offered) < cutoff
        except ValueError:
            stale = True
        if stale:
            pending.pop(target)
    state["manual_pending"] = pending
    if found:
        state["drafts_manual_posted"] = int(state.get("drafts_manual_posted", 0)) + found
    return found


def maybe_detect_manual(state: dict, auth) -> None:
    """The timeline check, at most every quarter hour.

    The consumer runs every five minutes because a button press should not wait.
    Reading our own timeline that often buys nothing — nobody posts by hand in
    under a minute — and it is a rate-limited endpoint shared with the radar
    itself.
    """
    if not state.get("manual_pending"):
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    last = state.get("manual_checked_at")
    if last:
        try:
            if (now - datetime.datetime.fromisoformat(last)).total_seconds() < \
                    MANUAL_CHECK_MINUTES * 60:
                return
        except ValueError:
            pass
    state["manual_checked_at"] = now.isoformat()
    found = detect_manual_posts(state, auth)
    log.info(f"Manual-post check: {found} newly detected, "
             f"{len(state.get('manual_pending') or {})} still open")


# ── Reading list (from 18.10.2026) ──

def pass_reason(t: dict, targets: dict) -> str:
    """Why this post came through the filter, in the terms drop_reason uses.

    A list post passed because somebody curated its author; anything else
    passed the impressions and follower floors. A tier-4 post also passed the
    topic rule, and the word that matched is the useful part of the reason.
    """
    source = t.get("_source")
    tier = tier_of(t, targets)
    if source == "list":
        group = (targets.get((t.get("_author") or "").lower()) or {}).get("group")
        why = f"Liste, Tier {tier}" + (f" ({group})" if group else "")
    else:
        impressions = (t.get("public_metrics") or {}).get("impression_count", 0)
        followers = t.get("_author_followers")
        label = "Erwähnung" if source == "mention" else "Suche"
        why = (f"{label}, Tier {tier}, {impressions} Impressionen ≥ {MIN_IMPRESSIONS}"
               + (f", {followers} Follower ≥ {MIN_AUTHOR_FOLLOWERS}"
                  if followers is not None else ""))
    if tier >= 4:
        hit = ON_TOPIC_RE.search(t.get("text") or "")
        if hit:
            why += f", Thema „{hit.group(0)}“"
    return why


def reading_list(items: list[dict], targets: dict, candidates: int,
                 since: datetime.datetime, now: datetime.datetime) -> str:
    """The Telegram message: link, author, why it passed, impressions.

    No draft and no keyboard, by construction — nothing in here reads a draft,
    and the message goes out without reply_markup.
    """
    lines = [f"\U0001f4d6 Reply-Radar — Leseliste, {len(items)} von {candidates} "
             f"Kandidaten\n"
             f"Fenster {since:%H:%M}–{now:%H:%M} UTC. Antworten auf X laufen "
             f"seit dem 18.10.2026 von Hand über Lars' eigenen Account, ohne "
             f"Takt und ohne Pflicht. Der Radar entwirft nichts mehr."]
    for i, t in enumerate(items, 1):
        author = t.get("_author") or "i"
        impressions = (t.get("public_metrics") or {}).get("impression_count", 0)
        lines.append(f"{i}. @{html.escape(author)} · {impressions} Impressionen\n"
                     f"Grund: {html.escape(pass_reason(t, targets))}\n"
                     f"https://x.com/{html.escape(author)}/status/{t['id']}")
    return "\n\n".join(lines)


def reading_list_run(state: dict, auth, since: datetime.datetime,
                     now: datetime.datetime, include_search: bool,
                     dry_run: bool) -> None:
    """The run from 18.10: gather, filter, rank, list. No model, no X write.

    The same reads as before, so the schedule decides the cost and nothing in
    here adds a call: no KB fetch, no citation index, no target lookup. One
    post per author per run (mentions exempt), the same rule the drafter had,
    so one busy account does not fill the list.
    """
    targets = load_targets()
    log.info(f"Targets configured: {len(targets)}  ·  reading list only "
             f"({RETIRED_REASON})")
    fetched = gather(auth, since, since_id=state.get("list_since_id"),
                     include_search=include_search, targets=targets)
    newest = newest_list_id(fetched)
    if newest:
        state["list_since_id"] = newest
    candidates = [t for t in fetched if worth_answering(t, state, since, targets)]
    log.info(f"Candidates after filtering: {len(candidates)}")
    if not candidates:
        report_source_failures(state)
        if not dry_run:
            state["last_run_at"] = now.isoformat()
            save_state(state)
        write_heartbeat("ok", "no candidates · reading list only")
        return

    picked: list[dict] = []
    authors: set[str] = set()
    for t in rank(candidates, targets):
        if len(picked) >= READ_LIST_MAX:
            break
        author = (t.get("_author") or "").lower()
        if t.get("_source") != "mention" and author and author in authors:
            continue
        authors.add(author)
        picked.append(t)

    body = reading_list(picked, targets, len(candidates), since, now)
    if dry_run:
        print(body)
    else:
        notify.send_telegram(body, channel=notify.STATS, parse_mode="HTML")
        for t in picked:
            mark_seen(state, t["id"])
    log.info(f"reading list: {len(picked)} posts of {len(candidates)} candidates"
             + ("  (dry run, not sent)" if dry_run else " sent"))
    report_source_failures(state)
    if not dry_run:
        state["last_run_at"] = now.isoformat()
        save_state(state)
    write_heartbeat("ok", f"reading list: {len(picked)} posts, no draft")


def run(dry_run: bool = False, limit: int = PER_RUN_MAX,
        include_search: bool = True) -> None:
    now = _utcnow()
    today = now.strftime("%Y-%m-%d")
    log.info("=" * 60)
    log.info(f"REPLY RADAR — {now:%Y-%m-%d %H:%M UTC}" + ("  *** DRY RUN ***" if dry_run else ""))

    auth = x_auth()
    if not auth:
        log.error("X credentials not available")
        write_heartbeat("error", "x credentials missing")
        return

    state = load_state()
    paused = x_meter.reads_paused()
    if paused:
        log.warning(f"Reads paused: {paused}")
        report_reads_paused(state, paused)
        state["last_run_at"] = now.isoformat()
        save_state(state)
        write_heartbeat("paused", paused)
        return
    since = lookback_since(state, now)
    log.info(f"Window: {since:%H:%M} -> {now:%H:%M} UTC "
             f"({(now - since).total_seconds() / 3600:.1f} h)"
             + ("" if include_search else "  ·  list + mentions only"))
    # Callbacks belong to --consume, which runs every five minutes. Claiming
    # them here too would mean whichever ran first swallowed the decision.
    maybe_report_decisions(state)
    # From 18.10 to 26.10 the run ended here, before the daily cap, the KB
    # fetch and the drafter: the cap counts drafts, and there were none to
    # count. From 27.10 the run goes on and the cap of two applies again.
    if drafter_retired(now):
        reading_list_run(state, auth, since, now, include_search, dry_run)
        return
    used = drafted_today(state, today)
    room = min(limit, max(0, DAILY_MAX - used))
    log.info(f"Drafted today: {used}/{DAILY_MAX} — room for {room} this run")
    if room == 0:
        state["last_run_at"] = now.isoformat()
        save_state(state)          # decisions read above must not be lost
        write_heartbeat("ok", f"daily cap reached ({used}/{DAILY_MAX})")
        return

    targets = load_targets()
    log.info(f"Targets configured: {len(targets)}")
    kb = load_kb()
    fetched = gather(auth, since, since_id=state.get("list_since_id"),
                     include_search=include_search, targets=targets)
    newest = newest_list_id(fetched)
    if newest:
        state["list_since_id"] = newest
    candidates = [t for t in fetched if worth_answering(t, state, since, targets)]
    by_tier = {}
    for t in candidates:
        by_tier[tier_of(t, targets)] = by_tier.get(tier_of(t, targets), 0) + 1
    log.info(f"Candidates after filtering: {len(candidates)} "
             f"(by tier: {dict(sorted(by_tier.items()))})")
    if not candidates:
        report_source_failures(state)
        state["last_run_at"] = now.isoformat()
        save_state(state)          # decisions read above must not be lost
        write_heartbeat("ok", "no candidates")
        return

    made = 0
    passed_gate = 0
    used_authors: set[str] = set()
    for tweet in rank(candidates, targets):
        if made >= room:
            break
        # Before the model is asked anything: an author who has had their turn
        # costs nothing to skip here and a draft to skip later.
        capped = author_cap_reason(state, tweet, used_authors, today)
        if capped:
            log.info(f"  skip {tweet['id']} — {capped}")
            continue
        drafted = draft_reply(tweet, kb, list(claim_history(state)))
        if not drafted:
            why = (record_refusal(state, tweet, today, targets) if not dry_run
                   else f"{_LAST_REFUSAL.get('kind')}: {str(_LAST_REFUSAL.get('detail'))[:200]}")
            # "nothing factual to say" is the silence reason the register and
            # the deadman read (config/expectations.yaml); it stays for every
            # refusal by the model, and only for those.
            final = _refusal_is_final(why)
            log.info(f"  skip {tweet['id']} (@{tweet.get('_author')}) — "
                     + (f"nothing factual to say [{why}]" if final else f"not drafted [{why}]"))
            if not dry_run and final:
                mark_seen(state, tweet["id"])
            continue
        text, cited = drafted
        clash = claim_conflict(state, text, now)
        if clash:
            log.info(f"  skip {tweet['id']} (@{tweet.get('_author')}) — {clash}")
            log.info(f"    {text}")
            if dry_run:
                print(f"\n--- CLAIM CLASH · @{tweet.get('_author')} ---\n"
                      f"{clash}\n-> {text}\n")
            else:
                mark_seen(state, tweet["id"])
            continue
        # Whatever it cited, plus the links in the post itself. The KB is
        # already in hand, so only the URLs it actually used are kept — a
        # source list naming every page we own would prove nothing.
        fetched = fetch_sources([u for u in cited if u not in kb] + post_links(tweet))
        sources = {u: kb[u] for u in cited if u in kb}
        sources.update(fetched)
        # What the post itself says is quotable. Its links are not, unless the
        # draft named them — post_links handles that above.
        sources.update(target_as_source(tweet))
        ok, problems, _scan = check(text, sources)
        made += 1
        if ok:
            passed_gate += 1
        log.info(f"  draft {made} for {tweet['id']} (@{tweet.get('_author')}, "
                 f"tier {tier_of(tweet, targets)}, {tweet.get('_source')}): "
                 f"{'PASS' if ok else 'BLOCKED'}")
        log.info(f"    {text}")
        if problems:
            log.info("    " + "; ".join(problems))
        if ok:
            remember_claims(state, text)
        if dry_run:
            print(f"\n--- {'PASS' if ok else 'BLOCKED'} · @{tweet.get('_author')} "
                  f"· {tweet.get('_source')} ---\n{tweet.get('text','')[:200]}\n"
                  f"-> {text}\n"
                  + "".join(f"   src: {u}\n" for u in sources)
                  + ("   ! " + "; ".join(problems) + "\n" if problems else ""))
            used_authors.add((tweet.get("_author") or "").lower())
            continue
        message_id = send_draft(made, tweet, text, ok, problems, sources)
        if message_id is not None:
            # CM6: from here the draft is open until it gets its one line in
            # data/reply_radar_decisions.jsonl — sent, discarded or expired.
            try:
                reply_decisions.register(tweet, ok, problems, message_id,
                                         chat_id=notify.chat_id_for(notify.STATS),
                                         now=now)
            except Exception as e:
                log.error(f"  decisions register failed for {tweet['id']}: "
                          f"{type(e).__name__}: {e}")
        mark_seen(state, tweet["id"])
        count_draft(state, today)
        count_author(state, tweet, today)
        count_source(state, tweet, today)
        used_authors.add((tweet.get("_author") or "").lower())
        if ok:
            # Only a draft with buttons can be decided on.
            state["drafts_sent"] = int(state.get("drafts_sent", 0)) + 1
            # Not while the API side is retired: retire_pending_offers()
            # would rewrite the message five minutes later and take the
            # draft and its link with it (finding of 31.10, CM1).
            if (tweet.get("_source") not in API_REPLYABLE_SOURCES
                    and not reply_branch_retired()):
                # Handed over for manual posting. From here the timeline
                # check is the only thing that can close it out.
                remember_manual(state, tweet, text, message_id)

    report_source_failures(state)
    if not dry_run:
        state["last_run_at"] = now.isoformat()
        save_state(state)
    write_heartbeat("ok", f"{made} drafts, {drafted_today(state, today)}/{DAILY_MAX} today")
    count_by_prompt(state, today, len(candidates), made, passed_gate)
    save_state(state)
    log.info(f"Done: {made} drafts this run, {passed_gate} through the gates "
             f"· prompt {PROMPT_VERSION}")


def offered_before_retirement(entry: dict) -> bool:
    """Was this offer made before 18.10, when it could still carry a key?

    An entry without a readable date counts as new: leaving a message alone
    costs nothing, and rewriting a draft is the fault this guards against.
    """
    when = entry.get("offered_at")
    try:
        offered = datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except ValueError:
        return False
    return offered.date() < REPLY_BRANCH_RETIRED_FROM


def retire_pending_offers(state: dict) -> int:
    """Strip the buttons off the drafts handed over before 18.10.

    An intent link is a URL button: pressing it never reaches this process, so
    refusing it in --consume is not possible. Editing the message is — Telegram
    drops the inline keyboard when the text is edited without one. Only the
    offers in `manual_pending` carry a known message id; older drafts left the
    watch after MANUAL_PENDING_HOURS and keep their link in the chat.

    Only offers from before 18.10 (ops RR-KEEP). Since the drafter came back
    on 27.10 (CM1) its drafts go out as plain text with no key, and that text
    is what Lars copies (CM14). On 31.10 this function rewrote message 7223 five
    minutes after the run and left a withdrawal notice where the draft had
    been. A newer entry leaves the watch untouched; an old one whose edit
    Telegram refuses stays and is tried again on the next run.
    """
    pending = state.get("manual_pending") or {}
    withdrawn, kept = 0, {}
    for target, entry in pending.items():
        if not offered_before_retirement(entry):
            log.info(f"  offer for {target} left standing — drafted after "
                     f"{REPLY_BRANCH_RETIRED_FROM}, text stays (RR-KEEP)")
            continue
        if edit_message(entry.get("chat_id"), entry.get("message_id"),
                        "\u23f9 Reply-Entwurf zurückgezogen — der Antwortzweig ist "
                        "seit dem 18.10.2026 stillgelegt. Antworten auf X laufen "
                        "von Hand über Lars' eigenen Account."):
            withdrawn += 1
            log.info(f"  offer for {target} withdrawn — {RETIRED_REASON}")
        else:
            kept[target] = entry
            log.warning(f"  offer for {target} not withdrawn — edit refused, "
                        f"next run tries again")
    state["manual_pending"] = kept
    state["offers_withdrawn_at"] = _utcnow().isoformat()
    return withdrawn


def consume_and_post(dry_run: bool = False) -> None:
    """Act on the button presses. Runs every five minutes.

    Posting happens only for `rr|post`, only when armed, and only after the
    draft has been through both gates again — against sources re-fetched now,
    not against the ones that were current when it was drafted.

    From 18.10 no rr|post ever posts (handle_decision refuses it first). The
    consumer keeps running anyway: it is also the only consumer of the
    LinkedIn buttons, and stopping it would drop those presses.
    """
    state = load_state()
    today = _utcnow().strftime("%Y-%m-%d")
    auth = x_auth()

    # A manually posted reply arrives without a button press, so this runs
    # before the queue is looked at and regardless of whether it is empty.
    # From 18.10 nothing is handed over any more, so instead of watching the
    # timeline the open offers are closed once and the reads stop.
    if reply_branch_retired():
        if not dry_run and state.get("manual_pending"):
            retire_pending_offers(state)
            save_state(state)
    elif auth and not dry_run:
        try:
            maybe_detect_manual(state, auth)
        except Exception as e:
            log.error(f"  manual-post check failed: {type(e).__name__}: {e}")
        save_state(state)

    # CM6: twelve hours decide what nobody answered — a discard without a
    # reason becomes "ohne Grund", a draft nobody pressed becomes verfallen.
    # Before the queue, so it runs when the queue is empty too.
    if not dry_run:
        # CM23: a reply Lars posted through the intent link closes its draft
        # as gesendet. Before the sweep, so a draft answered in time is never
        # booked verfallen first. A read only, on his read-only grant.
        try:
            from agents import lars_reply_match
            lars_reply_match.run()
        except Exception as e:
            log.error(f"  Lars reply match failed: {type(e).__name__}: {e}")
        try:
            sweep_decisions()
        except Exception as e:
            log.error(f"  decisions sweep failed: {type(e).__name__}: {e}")
        try:
            consume_reason_texts()
        except Exception as e:
            log.error(f"  free-text reasons failed: {type(e).__name__}: {e}")

    updates = telegram_inbox.claim("reply_radar_post", ["callback_query"], limit=50)
    if not updates:
        log.info("No decisions waiting")
        return

    decisions = state.setdefault("decisions", {})

    for u in updates:
        cq = u.get("callback_query") or {}
        data = cq.get("data") or ""
        # The LinkedIn buttons ride this consumer on purpose. claim() marks
        # every callback row consumed the moment it is read, so a second
        # consumer would race for the same rows and whichever lost would drop
        # them silently — and a `li|` row dropped here is a draft Lars pressed
        # a button on that never posted.
        if data.startswith("li|"):
            try:
                handle_linkedin(cq, data, dry_run)
            except Exception as e:
                log.error(f"  linkedin {data[:24]}: {type(e).__name__}: {e}\n"
                          f"{traceback.format_exc()}")
                notify.send_telegram(
                    f"\u26a0\ufe0f LinkedIn-Konsument fehlgeschlagen\n"
                    f"{type(e).__name__}: {str(e)[:200]}", channel=notify.ALERTS)
            continue
        # Moltbook reply drafts (ops MB1), on this consumer for the same reason.
        if data.startswith("mb|"):
            try:
                from agents import moltbook_drafts
                moltbook_drafts.handle(cq, data, dry_run)
            except Exception as e:
                log.error(f"  moltbook {data[:24]}: {type(e).__name__}: {e}\n"
                          f"{traceback.format_exc()}")
                notify.send_telegram(
                    f"\u26a0\ufe0f Moltbook-Konsument fehlgeschlagen\n"
                    f"{type(e).__name__}: {str(e)[:200]}", channel=notify.ALERTS)
            continue
        if not data.startswith("rr|"):
            continue
        try:
            _, verb, tweet_id = data.split("|", 2)
        except ValueError:
            log.warning(f"  unparseable callback_data: {data[:40]}")
            continue
        if verb == "noop":
            continue
        if verb.startswith(REASON_VERB_PREFIX):
            try:
                handle_reason(cq, verb, tweet_id, dry_run)
            except Exception as e:
                log.error(f"  reason {tweet_id}: {type(e).__name__}: {e}\n"
                          f"{traceback.format_exc()}")
            finally:
                save_state(state)
            continue
        try:
            handle_decision(state, decisions, auth, cq, verb, tweet_id, today, dry_run)
        except Exception as e:
            # The row left the queue the moment it was claimed, so a failure
            # here has to be loud rather than take the rest of the batch with
            # it. This is what a missing import cost on 2026-09-22 at 17:45.
            log.error(f"  {tweet_id}: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            decisions.setdefault(tweet_id, {})["result"] = f"error: {type(e).__name__}"
            notify.send_telegram(
                f"\u26a0\ufe0f Reply-Konsument: {tweet_id} fehlgeschlagen\n"
                f"{type(e).__name__}: {str(e)[:200]}", channel=notify.ALERTS)
        finally:
            save_state(state)


def handle_linkedin(cq: dict, data: str, dry_run: bool) -> None:
    """A button press on a LinkedIn draft. The only path to the write.

    Nothing posts without arriving here, and nothing arrives here without
    somebody having pressed a button in Telegram.
    """
    from agents import linkedin_post
    try:
        _, verb, key = data.split("|", 2)
    except ValueError:
        log.warning(f"  unparseable linkedin callback: {data[:40]}")
        return
    pending = linkedin_post.load_pending()
    entry = pending.get(key)
    msg = (cq.get("message") or {})
    chat_id = (msg.get("chat") or {}).get("id")
    msg_id = msg.get("message_id")

    if not entry:
        edit_message(chat_id, msg_id,
                     "\u26a0\ufe0f Entwurf nicht mehr im Speicher — "
                     "nichts gepostet.")
        log.warning(f"  linkedin {key}: kein Entwurf im Speicher")
        return
    if entry.get("result"):
        # Telegram re-delivers a callback if the first answer was slow, and a
        # second press must not post twice. The guard is the stored result, not
        # the button: the button stays on the message until it is edited away.
        log.info(f"  linkedin {key}: bereits {entry['result']}")
        return

    if verb == "drop":
        entry["result"] = "verworfen"
        pending[key] = entry
        linkedin_post.save_pending(pending)
        edit_message(chat_id, msg_id, f"\U0001f5d1 Verworfen — {entry.get('title') or key}")
        log.info(f"  linkedin {key}: verworfen")
        return
    if verb != "post":
        return
    if dry_run:
        log.info(f"  linkedin {key}: dry run, nichts gepostet")
        return

    posted = linkedin_post.post_share(entry["text"])
    entry["result"] = "gepostet"
    entry["urn"] = posted["urn"]
    pending[key] = entry
    linkedin_post.save_pending(pending)
    linkedin_post.record(key, posted, entry)
    edit_message(chat_id, msg_id,
                 f"\u2705 Auf LinkedIn gepostet\n{posted['url']}")
    log.info(f"  linkedin {key}: gepostet {posted['urn']}")


def handle_decision(state, decisions, auth, cq, verb, tweet_id, today, dry_run):
    """One button press, start to finish. Raises; the caller isolates it."""
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    decisions[tweet_id] = {
        "verb": verb,
        "prompt_version": PROMPT_VERSION,
        "source": draft_source(msg.get("text") or ""),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "by": (cq.get("from") or {}).get("username"),
    }
    if verb == "drop":
        log.info(f"  {tweet_id}: dropped")
        ask_reason(tweet_id, msg)
        return
    if verb != "post":
        return

    # First, before the draft is read or X is asked anything. A Posten button
    # on a mention draft sent before 18.10 still delivers its callback, and
    # nothing about the message says the branch has ended since.
    if reply_branch_retired():
        log.warning(f"  {tweet_id}: not posted — {RETIRED_REASON}")
        edit_message(chat_id, message_id,
                     "\u23f9 Nicht gepostet — der Antwortzweig ist seit dem "
                     "18.10.2026 stillgelegt.")
        decisions[tweet_id]["result"] = "retired"
        return

    # CM2: twelve hours, and the press is answered rather than swallowed. The
    # draft itself stays in the message for anyone who wants to answer by
    # hand — what expires is our permission to send it.
    if draft_expired(msg):
        log.info(f"  {tweet_id}: not posted — draft expired "
                 f"({DRAFT_TTL_HOURS} h)")
        edit_message(chat_id, message_id,
                     f"⏳ Verfallen — der Entwurf ist älter als "
                     f"{DRAFT_TTL_HOURS} Stunden und wird nicht mehr gesendet.")
        decisions[tweet_id]["result"] = "expired"
        reply_decisions.close(tweet_id, reply_decisions.EXPIRED, msg=msg)
        return

    parsed = parse_draft(msg.get("text") or "")
    if not parsed:
        log.error(f"  {tweet_id}: could not read the draft out of the message")
        edit_message(chat_id, message_id,
                     "\u26a0\ufe0f Entwurf nicht lesbar — nichts gepostet.")
        decisions[tweet_id]["result"] = "unparseable"
        return
    text, cited = parsed

    # CM3 criteria 3 and 4: without Lars's tokens nothing is sent, and the
    # press says so with the text and the post it answers. Asked before any
    # other check, so the answer is the same whatever else would hold the draft.
    lars = lars_auth()
    if lars is None:
        log.warning(f"  {tweet_id}: not posted — @{LARS_HANDLE} not unlocked")
        edit_message(chat_id, message_id,
                     f"⚠️ Account nicht freigeschaltet — nichts gesendet. "
                     f"Senden geht nur über @{LARS_HANDLE}, und dessen Tokens "
                     f"fehlen.\n\n{copy_block(text, tweet_id)}")
        decisions[tweet_id]["result"] = "lars_not_unlocked"
        return

    # CM8: nothing X can linkify leaves through this button. A reply with a
    # link costs $0.200 against $0.015 — thirteen times — and two replies a day
    # without one stay under the weekly $0.25. The same pattern books the price
    # in the ledger (x_meter.URL_RE), so a reply that got out some other way is
    # still charged at the link price. The text goes out with the refusal: the
    # answer can be posted by hand once the link is out of it.
    carried = x_meter.link_in(text)
    if carried:
        log.warning(f"  {tweet_id}: not posted — draft carries a link ({carried})")
        edit_message(chat_id, message_id,
                     f"⚠️ Nicht gepostet — der Entwurf enthält einen Link "
                     f"({html.escape(carried)}). Eine Antwort mit Link kostet bei X "
                     f"0,200 $ statt 0,015 $.\n\nZum Kopieren:\n"
                     f"<pre>{html.escape(text)}</pre>")
        decisions[tweet_id]["result"] = "link_in_draft"
        return

    # Point 4 of the rebuild, and the reason any of this changed: X answers a
    # reply to a third party with 403 regardless of tier. Only a mention is
    # ours to answer through the API. A list draft now carries the same Senden
    # key (CM2), so reaching here is a normal press, not a stale callback, and
    # this is where the difference between the sources is said.
    source = draft_source(msg.get("text") or "")
    if source not in API_REPLYABLE_SOURCES:
        log.warning(f"  {tweet_id}: source {source!r} is not API-replyable")
        edit_message(chat_id, message_id,
                     "⚠️ Nicht gepostet — auf einen fremden Post "
                     "antwortet die X-API nicht (403). Von Hand geht es; der "
                     "Zielpost ist in der Meldung verlinkt.\n\nZum Kopieren:\n"
                     f"<pre>{html.escape(text)}</pre>")
        decisions[tweet_id]["result"] = "api_forbidden"
        return

    # CM4: the weekly send cap. Only the button is held — the reading list, the
    # drafter and the CM5 measurement never ask this, so a week that is spent
    # keeps measuring and keeps drafting, and what it stops is the one thing
    # that costs money (Lars, CM10). The text goes out with the refusal so the
    # answer can still be posted by hand, which is how every list draft goes
    # out anyway.
    send_pause = x_meter.sends_paused()
    if send_pause:
        log.warning(f"  {tweet_id}: {send_pause}")
        edit_message(chat_id, message_id,
                     f"⏸ Nicht gepostet — {html.escape(send_pause)}.\n\n"
                     f"Zum Kopieren:\n<pre>{html.escape(text)}</pre>")
        notify.send_telegram(f"⏸ Wochendeckel Senden erreicht\n{send_pause}",
                             channel=notify.ALERTS)
        decisions[tweet_id]["result"] = "send_cap"
        return

    posted_today = int(state.get("posted_per_day", {}).get(today, 0))
    if posted_today >= DAILY_MAX:
        log.warning(f"  {tweet_id}: daily cap {DAILY_MAX} reached")
        edit_message(chat_id, message_id,
                     f"\u26a0\ufe0f Tagesdeckel {DAILY_MAX} erreicht — nichts gepostet.")
        decisions[tweet_id]["result"] = "capped"
        return

    ok_target, why, target = target_ok(auth, tweet_id)
    if not ok_target:
        log.warning(f"  {tweet_id}: {why}")
        edit_message(chat_id, message_id, f"\u26a0\ufe0f Nicht gepostet — {why}.")
        decisions[tweet_id]["result"] = why
        return

    # The gates run again here, so the post being answered has to be in the
    # corpus here too — otherwise a draft that passed at drafting time blocks
    # at posting time on a figure that is visible in the post it answers.
    sources = fetch_sources(cited)
    sources.update(target_as_source(target))
    ok, problems, _scan = check(text, sources)
    if not ok:
        log.error(f"  {tweet_id}: gates now block it — {'; '.join(problems)[:200]}")
        edit_message(chat_id, message_id,
                     "\u26a0\ufe0f Nicht gepostet — der Entwurf fällt jetzt durch die "
                     "Gates:\n<pre>" + html.escape("\n".join(problems)[:600]) + "</pre>")
        decisions[tweet_id]["result"] = "gate_block"
        return

    if dry_run or not armed():
        reason = "dry run" if dry_run else f"{ARM_FLAG} not set"
        log.info(f"  {tweet_id}: would post ({reason}) — {text[:70]}…")
        decisions[tweet_id]["result"] = f"not posted: {reason}"
        return

    # auth= is passed explicitly: x_post.post falls back to the @MolTrust pair
    # when it gets None, and lars is known not to be None here.
    reply_id = x_post.post(text, reply_to=tweet_id, kind="reply", auth=lars)
    if not reply_id:
        log.error(f"  {tweet_id}: the post failed")
        edit_message(chat_id, message_id,
                     "\u26a0\ufe0f Posten fehlgeschlagen — siehe logs/reply_radar.log. "
                     f"Von Hand geht es.\n\n{copy_block(text, tweet_id)}")
        decisions[tweet_id]["result"] = "post_failed"
        return

    link = f"https://x.com/{LARS_HANDLE}/status/{reply_id}"
    posted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    state.setdefault("posted_per_day", {})[today] = posted_today + 1
    decisions[tweet_id].update({
        "result": "posted", "route": "api", "account": LARS_HANDLE,
        "reply_id": reply_id, "link": link, "posted_at": posted_at,
        "followers_at_post": followers_now(lars),
    })
    log.info(f"  {tweet_id}: posted {link}")
    # CM5: the first reply sent here opens Lars's measurement window. A booking
    # that fails must not undo a post that went out, so it is logged loudly.
    try:
        from scripts import lars_reply_metrics
        lars_reply_metrics.record_sent(reply_id, tweet_id, posted_at)
    except Exception as e:
        log.error(f"  {tweet_id}: CM5 booking failed: {type(e).__name__}: {e}")
    reply_decisions.close(tweet_id, reply_decisions.SENT, msg=msg)
    edit_message(chat_id, message_id, f"\u2705 Gepostet\n{link}\n\n{html.escape(text)}")


# ── CM6: the reason behind a discard ──
#
# Verwerfen is answered with one question: four short keys and a free-text key.
# The callback carries the reason code in the verb (rr|why-ton|<tweet id>), so
# it fits Telegram's 64 bytes and needs no lookup to know what was chosen. Free
# text arrives as an ordinary message replying to the question or to the
# prompt the free-text key sends; telegram_inbox.claim(reply_to=...) takes only
# those, so ThreadWatch's commands and anything else typed in the chat stay
# where they are.

REASON_VERB_PREFIX = "why-"
REASON_FREE_PROMPT = ("\u270d\ufe0f Grund als Antwort auf diese Nachricht "
                      "schreiben.")


def reason_keys(tweet_id: str) -> dict:
    """Four short keys and Freitext (CM6 criterion 2)."""
    def key(code: str, label: str) -> dict:
        return {"text": label,
                "callback_data": draft_id(tweet_id, REASON_VERB_PREFIX + code)}
    r = reply_decisions.REASONS
    return {"inline_keyboard": [
        [key("ton", r["ton"]), key("inhalt", r["inhalt"])],
        [key("ziel", r["ziel"]), key("timing", r["timing"])],
        [key(reply_decisions.FREE_TEXT, "\u270d\ufe0f Freitext")],
    ]}


def ask_reason(tweet_id: str, msg: dict) -> None:
    """After Verwerfen: one question, then the twelve-hour clock runs."""
    if reply_decisions.is_closed(tweet_id):
        log.info(f"  {tweet_id}: already closed, no reason asked")
        return
    question = (f"\U0001f5d1 Verworfen — warum?\n"
                f"Zielpost: https://x.com/i/status/{html.escape(tweet_id)}\n\n"
                f"Kurzknopf oder Freitext. Ohne Antwort binnen "
                f"{reply_decisions.REASON_TTL_HOURS} Stunden steht "
                f"„{reply_decisions.NO_REASON}“.")
    qid = notify.send_telegram_message(question, channel=notify.STATS,
                                       reply_markup=reason_keys(tweet_id))
    # Marked even when the question did not get out: the clock still runs,
    # and the draft closes as "ohne Grund" rather than staying open.
    reply_decisions.mark_asked(tweet_id, msg, qid)


def handle_reason(cq: dict, verb: str, tweet_id: str, dry_run: bool) -> None:
    """A press on one of the reason keys."""
    code = verb[len(REASON_VERB_PREFIX):]
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    if not reply_decisions.asked_entry(tweet_id):
        log.info(f"  {tweet_id}: reason {code!r} for a draft that is already closed")
        return
    if dry_run:
        log.info(f"  {tweet_id}: reason {code!r} (dry run, nothing recorded)")
        return
    if code == reply_decisions.FREE_TEXT:
        pid = notify.send_telegram_message(
            REASON_FREE_PROMPT, channel=notify.STATS,
            reply_markup={"force_reply": True, "input_field_placeholder": "Grund"})
        if pid is not None:
            reply_decisions.mark_prompted(tweet_id, pid)
        return
    reason = reply_decisions.REASONS.get(code)
    if not reason:
        log.warning(f"  {tweet_id}: unknown reason code {code!r}")
        return
    if reply_decisions.close(tweet_id, reply_decisions.DROPPED, reason=reason):
        edit_message(chat_id, message_id,
                     f"\U0001f5d1 Verworfen — Grund: {html.escape(reason)}")
        log.info(f"  {tweet_id}: discarded, reason {reason!r}")


def consume_reason_texts() -> int:
    """Free-text answers: a message replying to a reason question or prompt."""
    waiting = reply_decisions.awaiting_reason()
    if not waiting:
        return 0
    updates = telegram_inbox.claim("reply_radar_reason", ["message"], limit=50,
                                   reply_to=list(waiting))
    taken = 0
    for u in updates:
        m = u.get("message") or {}
        ref = (m.get("reply_to_message") or {}).get("message_id")
        tweet_id = waiting.get(int(ref)) if ref is not None else None
        text = (m.get("text") or "").strip()
        if not tweet_id or not text:
            continue
        entry = reply_decisions.asked_entry(tweet_id) or {}
        reason = text[:reply_decisions.FREE_TEXT_MAX]
        if reply_decisions.close(tweet_id, reply_decisions.DROPPED, reason=reason):
            edit_message(entry.get("chat_id") or (m.get("chat") or {}).get("id"),
                         entry.get("question_message_id"),
                         f"\U0001f5d1 Verworfen — Grund: {html.escape(reason)}")
            log.info(f"  {tweet_id}: discarded, free-text reason")
            taken += 1
    return taken


def sweep_decisions() -> int:
    """Close what twelve hours have decided, and say so on the question."""
    closed = reply_decisions.due()
    for tweet_id, result, reason, entry in closed:
        if result == reply_decisions.DROPPED and entry.get("question_message_id"):
            edit_message(entry.get("chat_id"), entry.get("question_message_id"),
                         f"\U0001f5d1 Verworfen — {reply_decisions.NO_REASON} "
                         f"(keine Antwort binnen "
                         f"{reply_decisions.REASON_TTL_HOURS} Stunden)")
        log.info(f"  {tweet_id}: closed as {result}"
                 + (f" ({reason})" if reason else ""))
    return len(closed)


def report_counts(send: bool) -> None:
    """The tally, on demand. Reads decisions first so it is not stale."""
    # Reads only. Claiming here would race the five-minute consumer, and
    # whichever ran first would swallow the decision.
    c = decision_counts(load_state())
    share = f"{100.0 * c['post'] / c['decided']:.0f} %" if c["decided"] else "—"
    text = (f"\U0001f4ca Reply-Radar — Entscheidungen\n\n"
            f"Entwürfe gesendet: {c['sent']}\n"
            f"Posten: {c['post']} ({share})\n"
            f"Verwerfen: {c['drop']}\n"
            f"Offen: {c['open']}\n"
            f"Gepostet: {c['posted']} "
            f"({c['posted_manual']} von Hand, {c['posted_api']} per API)\n"
            f"Übergeben, noch nicht gepostet: {c['handed_over']}")
    print(text)
    if send:
        # A measurement: daily report, not Telegram (ops NOTIFY-3c).
        from app import tagesbericht_eingang
        tagesbericht_eingang.ablegen("reply_radar", text)


if __name__ == "__main__":
    try:
        if "--counts" in sys.argv:
            report_counts(send="--send" in sys.argv)
            raise SystemExit(0)
        if "--filter-report" in sys.argv:
            a = x_auth()
            if not a:
                log.error("X credentials not available")
                raise SystemExit(1)
            filter_report(a, load_state(), load_targets(), send="--send" in sys.argv)
            raise SystemExit(0)
        if "--consume" in sys.argv:
            consume_and_post(dry_run="--dry-run" in sys.argv)
            raise SystemExit(0)
        lim = PER_RUN_MAX
        if "--limit" in sys.argv:
            i = sys.argv.index("--limit")
            if i + 1 < len(sys.argv):
                lim = int(sys.argv[i + 1])
        run(include_search="--no-search" not in sys.argv,
            dry_run="--dry-run" in sys.argv, limit=lim)
    except Exception as e:
        log.error(f"FATAL: {e}\n{traceback.format_exc()}")
        write_heartbeat("crash", str(e))
        notify.send_telegram(f"\U0001f6a8 Reply radar crashed\n{str(e)[:300]}",
                             channel=notify.ALERTS)
        sys.exit(1)
