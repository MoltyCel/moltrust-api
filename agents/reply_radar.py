"""Reply radar — drafts replies, posts nothing.

Every two hours it reads three sources, picks what is worth answering, drafts a
reply for each, runs both gates over it, and sends the survivors to the stats
channel. Nothing reaches X from this file. There is no posting path in it at
all, deliberately: a write path that nobody has exercised is worse than no
write path, and the approval loop it would need does not exist yet (see
docs/reply-radar.md).

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import requests
from requests_oauthlib import OAuth1

from app import notify, telegram_inbox
from agents import voice_gate

DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")
STATE_FILE = os.path.join(DATA_DIR, "reply_radar_state.json")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "reply_radar_heartbeat.json")

OUR_USER_ID = "2023702578836779008"          # @moltrust
TARGETS_LIST_ID = "2101805022954557791"      # private list, empty until approved
MODEL = "claude-opus-5"

DAILY_MAX = 8            # the brief says 5-8 a day
PER_RUN_MAX = 3          # twelve runs a day, so this is a ceiling, not a target
LOOKBACK_HOURS = 3       # the cadence is 2h; the extra hour covers a missed run

# Applies to search and mention hits only. A list member was curated by hand;
# making it clear a second numeric bar would be curating twice.
MIN_IMPRESSIONS = 25

TARGETS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "reply_targets.json")

# Tier 4 and anyone over a million followers is only worth a reply when the post
# is about our actual subject. Everything else they post is somebody else's
# conversation and we would be the account that turns up uninvited.
BIG_ACCOUNT_FOLLOWERS = 1_000_000
ON_TOPIC_RE = re.compile(
    r"\b(agent[- ]?(identity|identities|authorization|authorisation|trust|credential)"
    r"|erc[- ]?8004|x402|did:|verifiable credential|agent registry"
    r"|know your agent|agent passport)\b", re.I)

# Nothing that names us may go out. Gate 2 (d) only guards the opener.
PRODUCT_RE = re.compile(r"\b(moltrust|moltguard|moltproof|moltbook|molt)\b", re.I)

SEARCH_QUERIES = [
    '("agent identity" OR "agent authorization" OR "agent trust") -is:retweet lang:en',
    '("ERC-8004" OR "erc8004") -is:retweet lang:en',
    '("prediction market" (manipulation OR wash OR coordinated)) -is:retweet lang:en',
    '("MCP server" (security OR audit OR malicious)) -is:retweet lang:en',
    '("x402" OR "agent payments") -is:retweet lang:en',
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
- You may also cite a link that appears in the post you are answering; it is
  fetched too. Nothing else. Do not cite a page from memory — a remembered URL
  and an invented one are indistinguishable, which is the whole reason this
  check exists. If neither the pages below nor the post give you a figure you
  can stand behind, say SKIP.
- Answering with our own measurement is the strongest reply available: it is
  published, dated and anyone can open it.
- No hashtags, no emoji, no greeting, no "great point", no thanks.
- Do not open by evaluating the post or its author.
- State the thing directly. No "not X but Y" constructions.

If the post does not give you something factual to answer with, or if you
cannot point at a page that carries your figures, return exactly:
SKIP

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


RECENT_CLAIMS_KEPT = 12

# Tell the stats channel once, when the decisions first reach this many. A
# running tally nobody asked for is noise; the first twenty are the sample that
# says whether the drafts are worth anything.
DECISION_REPORT_AT = 20

# The write path. Posting needs --consume and this flag in the environment,
# the same shape as scripts/revoke_inactive.py: a switch that lives only in an
# argument is one edited crontab line away from firing.
ARM_FLAG = "REPLY_RADAR_ARMED"
MAX_TARGET_AGE_HOURS = 24
DRAFT_RE = re.compile(r"Entwurf \((\d+)/280\):\s*\n(.+?)(?:\n\n|\Z)", re.S)
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


def target_ok(auth, tweet_id: str) -> tuple[bool, str]:
    """Does the post we would answer still exist, and is it still fresh?"""
    body = _get(auth, f"https://api.twitter.com/2/tweets/{tweet_id}",
                {"tweet.fields": "created_at"})
    data = body.get("data") if body else None
    if not data:
        return False, "the post is gone or unreadable"
    created = data.get("created_at")
    if created:
        when = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() / 3600
        if age > MAX_TARGET_AGE_HOURS:
            return False, f"the post is {age:.0f}h old, past the {MAX_TARGET_AGE_HOURS}h limit"
    return True, ""


def edit_message(chat_id, message_id, text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not (token and chat_id and message_id):
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/editMessageText",
                   json={"chat_id": chat_id, "message_id": message_id, "text": text,
                         "parse_mode": "HTML", "disable_web_page_preview": True},
                   timeout=20)
    except Exception as e:
        log.warning(f"  editMessageText failed: {type(e).__name__}")


def decision_counts(state: dict) -> dict:
    """sent / post / drop / open. `open` is what nobody has looked at yet.

    `drafts_sent` only started being counted when the keyboard shipped, and
    drafts sent before that were still decided on. Sent can therefore never be
    reported as fewer than decided — otherwise the first Sunday line reads
    "0 gesendet · 1 Verwerfen", which is arithmetic nobody should have to
    explain.
    """
    decisions = state.get("decisions", {})
    post = sum(1 for d in decisions.values() if d.get("verb") == "post")
    drop = sum(1 for d in decisions.values() if d.get("verb") == "drop")
    decided = post + drop
    sent = max(int(state.get("drafts_sent", 0)), decided)
    return {"sent": sent, "post": post, "drop": drop,
            "open": max(0, sent - decided), "decided": decided}


def maybe_report_decisions(state: dict) -> None:
    """One message when the decisions first reach DECISION_REPORT_AT."""
    c = decision_counts(state)
    if c["decided"] < DECISION_REPORT_AT or state.get("decisions_reported"):
        return
    share = (100.0 * c["post"] / c["decided"]) if c["decided"] else 0.0
    notify.send_telegram(
        f"\U0001f4ca Reply-Radar — {c['decided']} Entscheidungen erreicht\n\n"
        f"Entwürfe gesendet: {c['sent']}\n"
        f"Posten: {c['post']} ({share:.0f} %)\n"
        f"Verwerfen: {c['drop']}\n"
        f"Offen: {c['open']}\n\n"
        f"Erste belastbare Stichprobe: taugen die Entwürfe etwas.",
        channel=notify.STATS)
    state["decisions_reported"] = True


def recent_claims(state: dict) -> list[str]:
    return list(state.get("recent_claims", []))


def remember_claims(state: dict, text: str) -> None:
    """Keep what a sent draft leant on, so the next one reaches elsewhere.

    The first run with the KB produced three replies in a row built on the same
    blog post and the same two figures. Each was true and well sourced; three
    of them in one afternoon is a bot with one fact.
    """
    found = voice_gate.claims_in(text, [], 3)
    keep = [c for c in recent_claims(state) if c not in found]
    state["recent_claims"] = (found + keep)[:RECENT_CLAIMS_KEPT]


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


FIELDS = "created_at,public_metrics,author_id,conversation_id,lang,referenced_tweets"
USER_FIELDS = "username,name,public_metrics"


def _get(auth, url: str, params: dict) -> dict:
    try:
        r = requests.get(url, params=params, auth=auth, timeout=30)
    except Exception as e:
        log.error(f"GET {url} failed: {e}")
        return {}
    if r.status_code != 200:
        log.warning(f"GET {url} -> {r.status_code}: {r.text[:180]}")
        return {}
    return r.json()


def gather(auth, since: datetime.datetime) -> list[dict]:
    """Candidate posts from all three sources, newest first, deduped by id."""
    start = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict[str, dict] = {}
    authors: dict[str, dict] = {}

    def absorb(body: dict, source: str):
        for u in body.get("includes", {}).get("users", []):
            authors[u["id"]] = u
        for t in body.get("data", []) or []:
            t["_source"] = source
            out.setdefault(t["id"], t)

    absorb(_get(auth, f"https://api.twitter.com/2/lists/{TARGETS_LIST_ID}/tweets",
                {"max_results": 100, "tweet.fields": FIELDS,
                 "expansions": "author_id", "user.fields": USER_FIELDS}), "list")
    for q in SEARCH_QUERIES:
        absorb(_get(auth, "https://api.twitter.com/2/tweets/search/recent",
                    {"query": q, "max_results": 25, "start_time": start,
                     "tweet.fields": FIELDS, "expansions": "author_id",
                     "user.fields": USER_FIELDS}), "search")
    absorb(_get(auth, f"https://api.twitter.com/2/users/{OUR_USER_ID}/mentions",
                {"max_results": 25, "start_time": start, "tweet.fields": FIELDS,
                 "expansions": "author_id", "user.fields": USER_FIELDS}), "mention")

    for t in out.values():
        u = authors.get(t.get("author_id"), {})
        t["_author"] = u.get("username", "?")
        t["_author_followers"] = (u.get("public_metrics") or {}).get("followers_count")
    return list(out.values())


def worth_answering(t: dict, state: dict, since: datetime.datetime,
                    targets: dict) -> bool:
    if t["id"] in set(state.get("seen", [])):
        return False
    if t.get("author_id") == OUR_USER_ID:
        return False
    if any(r.get("type") == "retweeted" for r in t.get("referenced_tweets") or []):
        return False
    if t.get("lang") not in (None, "en"):
        return False
    created = t.get("created_at")
    if created:
        when = datetime.datetime.fromisoformat(created.replace("Z", "+00:00"))
        if when < since:
            return False
    # A post with nothing in it gives a reply nothing to hold on to.
    if len((t.get("text") or "").split()) < 8:
        return False

    # The curated list has already answered "is this worth watching". Applying
    # an impression floor on top would be curating twice, and the accounts that
    # matter most to us are the small ones.
    if t.get("_source") != "list":
        if (t.get("public_metrics") or {}).get("impression_count", 0) < MIN_IMPRESSIONS:
            return False

    # Tier 4 — prediction markets, and anyone over a million followers — only
    # when the post is about the thing we have something to say about.
    if tier_of(t, targets) >= 4 and not ON_TOPIC_RE.search(t.get("text") or ""):
        return False
    return True


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


def draft_reply(tweet: dict, kb: dict[str, str],
                avoid: list[str] | None = None) -> tuple[str, list[str]] | None:
    key = load_anthropic_key()
    if not key:
        log.error("No Anthropic API key available")
        return None
    docs = voice_gate.load_voice_docs()
    system = (SYSTEM_PROMPT
              + "\n\n=== anti-KI-Sprech.md (negative list) ===\n" + docs["anti_ki_sprech"]
              + "\n\n=== my-voice-en.md (positive model) ===\n" + docs["my_voice_en"]
              + "\n\n=== our published pages, already fetched — cite these by URL ===\n"
              + kb_digest(kb))
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
                       json={"model": MODEL, "max_tokens": 1500,
                             "system": system,
                             "messages": [{"role": "user", "content": user}]},
                       timeout=120)
    except Exception as e:
        log.error(f"Claude call failed: {e}")
        return None
    if r.status_code != 200:
        log.error(f"Claude API {r.status_code}: {r.text[:200]}")
        return None
    blocks = r.json().get("content", [])
    raw = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if not raw or raw.upper().startswith("SKIP"):
        return None
    try:
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
    except Exception as e:
        log.warning(f"  draft JSON unparseable ({e}); treating as a skip")
        return None
    text = str(data.get("reply", "")).strip().strip('"')
    sources = [s for s in (data.get("sources") or []) if isinstance(s, str)]
    if not text:
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
    return (not problems), problems, scan


# ── Output ──

def draft_id(tweet_id: str, verb: str) -> str:
    """callback_data for the keyboard. Telegram caps it at 64 bytes."""
    return f"rr|{verb}|{tweet_id}"[:64]


def send_draft(idx: int, tweet: dict, text: str, ok: bool,
               problems: list[str], sources: dict[str, str]) -> None:
    url = f"https://x.com/{tweet.get('_author', 'i')}/status/{tweet['id']}"
    metrics = tweet.get("public_metrics") or {}
    head = "\U0001f4dd Reply-Entwurf" if ok else "\u26a0\ufe0f Reply-Entwurf BLOCKIERT"
    body = (f"{head} {idx}\n"
            f"Quelle ({tweet.get('_source')}): {url}\n"
            f"{metrics.get('impression_count', 0)} Impressionen · "
            f"{metrics.get('like_count', 0)} Likes\n\n"
            f"<pre>{html.escape((tweet.get('text') or '')[:400])}</pre>\n\n"
            f"Entwurf ({len(text)}/280):\n<pre>{html.escape(text)}</pre>\n\n")
    if sources:
        body += ("Belege (im Lauf geholt, Gate 2 (h) geprüft):\n"
                 + "\n".join(f"· {html.escape(u)}" for u in sources) + "\n\n")
    if problems:
        body += "<pre>" + html.escape("\n".join(problems)[:900]) + "</pre>\n\n"

    # Only a draft that cleared both gates gets a decision to make. A blocked
    # one is shown so the failure is visible, not so it can be waved through.
    markup = None
    if ok:
        markup = {"inline_keyboard": [[
            {"text": "\u2705 Posten", "callback_data": draft_id(tweet["id"], "post")},
            {"text": "\U0001f5d1 Verwerfen", "callback_data": draft_id(tweet["id"], "drop")},
        ]]}
        body += ("Freigabe postet die Reply innerhalb von fünf Minuten, nach erneuter "
                 "Gate-Prüfung gegen frisch geholte Belege.")
    else:
        body += "Nichts wird gepostet."
    notify.send_telegram(body, channel=notify.STATS, parse_mode="HTML",
                         reply_markup=markup)


def run(dry_run: bool = False, limit: int = PER_RUN_MAX) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    since = now - datetime.timedelta(hours=LOOKBACK_HOURS)
    log.info("=" * 60)
    log.info(f"REPLY RADAR — {now:%Y-%m-%d %H:%M UTC}" + ("  *** DRY RUN ***" if dry_run else ""))

    auth = x_auth()
    if not auth:
        log.error("X credentials not available")
        write_heartbeat("error", "x credentials missing")
        return

    state = load_state()
    # Callbacks belong to --consume, which runs every five minutes. Claiming
    # them here too would mean whichever ran first swallowed the decision.
    maybe_report_decisions(state)
    used = drafted_today(state, today)
    room = min(limit, max(0, DAILY_MAX - used))
    log.info(f"Drafted today: {used}/{DAILY_MAX} — room for {room} this run")
    if room == 0:
        save_state(state)          # decisions read above must not be lost
        write_heartbeat("ok", f"daily cap reached ({used}/{DAILY_MAX})")
        return

    targets = load_targets()
    log.info(f"Targets configured: {len(targets)}")
    kb = load_kb()
    candidates = [t for t in gather(auth, since)
                  if worth_answering(t, state, since, targets)]
    by_tier = {}
    for t in candidates:
        by_tier[tier_of(t, targets)] = by_tier.get(tier_of(t, targets), 0) + 1
    log.info(f"Candidates after filtering: {len(candidates)} "
             f"(by tier: {dict(sorted(by_tier.items()))})")
    if not candidates:
        save_state(state)          # decisions read above must not be lost
        write_heartbeat("ok", "no candidates")
        return

    made = 0
    for tweet in rank(candidates, targets):
        if made >= room:
            break
        drafted = draft_reply(tweet, kb, recent_claims(state))
        if not drafted:
            log.info(f"  skip {tweet['id']} (@{tweet.get('_author')}) — nothing factual to say")
            if not dry_run:
                mark_seen(state, tweet["id"])
            continue
        text, cited = drafted
        # Whatever it cited, plus the links in the post itself. The KB is
        # already in hand, so only the URLs it actually used are kept — a
        # source list naming every page we own would prove nothing.
        fetched = fetch_sources([u for u in cited if u not in kb] + post_links(tweet))
        sources = {u: kb[u] for u in cited if u in kb}
        sources.update(fetched)
        ok, problems, _scan = check(text, sources)
        made += 1
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
            continue
        send_draft(made, tweet, text, ok, problems, sources)
        mark_seen(state, tweet["id"])
        count_draft(state, today)
        if ok:
            # Only a draft with buttons can be decided on.
            state["drafts_sent"] = int(state.get("drafts_sent", 0)) + 1

    if not dry_run:
        save_state(state)
    write_heartbeat("ok", f"{made} drafts, {drafted_today(state, today)}/{DAILY_MAX} today")
    log.info(f"Done: {made} drafts this run")


def consume_and_post(dry_run: bool = False) -> None:
    """Act on the button presses. Runs every five minutes.

    Posting happens only for `rr|post`, only when armed, and only after the
    draft has been through both gates again — against sources re-fetched now,
    not against the ones that were current when it was drafted. A post that
    passed at 14:05 and whose source has since changed is not a post we still
    stand behind.
    """
    state = load_state()
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    updates = telegram_inbox.claim("reply_radar_post", ["callback_query"], limit=50)
    if not updates:
        log.info("No decisions waiting")
        save_state(state)
        return

    auth = x_auth()
    posted_today = int(state.get("posted_per_day", {}).get(today, 0))
    decisions = state.setdefault("decisions", {})

    for u in updates:
        cq = u.get("callback_query") or {}
        data = cq.get("data") or ""
        if not data.startswith("rr|"):
            continue
        try:
            _, verb, tweet_id = data.split("|", 2)
        except ValueError:
            continue
        if verb == "noop":
            continue
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        decisions[tweet_id] = {
            "verb": verb,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "by": (cq.get("from") or {}).get("username"),
        }
        if verb != "post":
            log.info(f"  {tweet_id}: dropped")
            continue

        parsed = parse_draft(msg.get("text") or "")
        if not parsed:
            log.error(f"  {tweet_id}: could not read the draft out of the message")
            edit_message(chat_id, message_id,
                         "\u26a0\ufe0f Entwurf nicht lesbar — nichts gepostet.")
            decisions[tweet_id]["result"] = "unparseable"
            continue
        text, cited = parsed

        if posted_today >= DAILY_MAX:
            log.warning(f"  {tweet_id}: daily cap {DAILY_MAX} reached")
            edit_message(chat_id, message_id,
                         f"\u26a0\ufe0f Tagesdeckel {DAILY_MAX} erreicht — nichts gepostet.")
            decisions[tweet_id]["result"] = "capped"
            continue

        ok_target, why = target_ok(auth, tweet_id)
        if not ok_target:
            log.warning(f"  {tweet_id}: {why}")
            edit_message(chat_id, message_id, f"\u26a0\ufe0f Nicht gepostet — {why}.")
            decisions[tweet_id]["result"] = why
            continue

        sources = fetch_sources(cited)
        ok, problems, _scan = check(text, sources)
        if not ok:
            log.error(f"  {tweet_id}: gates now block it — {'; '.join(problems)[:200]}")
            edit_message(chat_id, message_id,
                         "\u26a0\ufe0f Nicht gepostet — der Entwurf fällt jetzt durch die "
                         "Gates:\n<pre>" + html.escape("\n".join(problems)[:600]) + "</pre>")
            decisions[tweet_id]["result"] = "gate_block"
            continue

        if dry_run or not armed():
            reason = "dry run" if dry_run else f"{ARM_FLAG} not set"
            log.info(f"  {tweet_id}: would post ({reason}) — {text[:70]}…")
            decisions[tweet_id]["result"] = f"not posted: {reason}"
            continue

        reply_id = x_post.post(text, reply_to=tweet_id)
        if not reply_id:
            log.error(f"  {tweet_id}: the post failed")
            edit_message(chat_id, message_id,
                         "\u26a0\ufe0f Posten fehlgeschlagen — siehe logs/reply_radar.log.")
            decisions[tweet_id]["result"] = "post_failed"
            continue

        link = f"https://x.com/MolTrust/status/{reply_id}"
        posted_today += 1
        state.setdefault("posted_per_day", {})[today] = posted_today
        decisions[tweet_id].update({"result": "posted", "reply_id": reply_id, "link": link})
        log.info(f"  {tweet_id}: posted {link}")
        edit_message(chat_id, message_id, f"\u2705 Gepostet\n{link}\n\n{html.escape(text)}")

    save_state(state)


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
            f"Offen: {c['open']}")
    print(text)
    if send:
        notify.send_telegram(text, channel=notify.STATS)


if __name__ == "__main__":
    try:
        if "--counts" in sys.argv:
            report_counts(send="--send" in sys.argv)
            raise SystemExit(0)
        if "--consume" in sys.argv:
            consume_and_post(dry_run="--dry-run" in sys.argv)
            raise SystemExit(0)
        lim = PER_RUN_MAX
        if "--limit" in sys.argv:
            i = sys.argv.index("--limit")
            if i + 1 < len(sys.argv):
                lim = int(sys.argv[i + 1])
        run(dry_run="--dry-run" in sys.argv, limit=lim)
    except Exception as e:
        log.error(f"FATAL: {e}\n{traceback.format_exc()}")
        write_heartbeat("crash", str(e))
        notify.send_telegram(f"\U0001f6a8 Reply radar crashed\n{str(e)[:300]}",
                             channel=notify.ALERTS)
        sys.exit(1)
