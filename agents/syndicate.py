"""Blog syndication — one new post becomes an X thread, a LinkedIn draft and a
Bluesky mirror.

Polls https://moltrust.ch/blog/feed.xml every 30 minutes. A feed item that is
new to the state file gets drafted into a 4-6 tweet thread: the hook carries no
link, the link sits in the last tweet. The draft passes the (a)-(f) pre-send
scan in agents/voice_gate.py before anything is posted; a blocked draft goes to
Telegram instead of to X and is retried on the next runs, then left alone.

The LinkedIn side is a draft only — it goes to Telegram for Lars to paste into
the company page. The Bluesky mirror posts itself, but only once
BLUESKY_HANDLE and BLUESKY_APP_PASSWORD exist in ~/.moltrust_secrets; without
them the run logs the skip and carries on.

First run on a fresh state file seeds every current feed item as seen and posts
nothing, so switching this on does not fire forty threads at the archive.

Cron: every 30 min.

    python agents/syndicate.py                 # the scheduled run
    python agents/syndicate.py --dry-run       # draft the newest item, post nothing
    python agents/syndicate.py --dry-run --guid <url>   # same, for one named item
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

import anthropic
import httpx
# The feed is our own server over HTTPS, which is exactly the assumption that
# stops holding the day that server is the thing that went wrong.
from defusedxml import ElementTree as ET

from app import notify
from agents import voice_gate, x_post

FEED_URL = "https://moltrust.ch/blog/feed.xml"
MODEL_DRAFT = "claude-opus-5"
DATA_DIR = os.path.expanduser("~/moltstack/data")
LOG_DIR = os.path.expanduser("~/moltstack/logs")
STATE_FILE = os.path.join(DATA_DIR, "syndicate_state.json")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "syndicate_heartbeat.json")

MAX_DRAFT_ATTEMPTS = 3
THREAD_MIN, THREAD_MAX = 4, 6
BLUESKY_LIMIT = 300

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("syndicate")
# httpx logs every request URL at INFO, which writes the Telegram bot token into
# the log file in clear text. Keep it at WARNING here.
logging.getLogger("httpx").setLevel(logging.WARNING)
os.makedirs(DATA_DIR, exist_ok=True)

# my-voice-en §0: the feed's own <category> picks the sarcasm tier.
REGISTER_TIER = {
    "opinion": "Opinion — full dry bite. Sarcasm through number, never adjective.",
    "analysis": "Analysis — measured. The number carries the point, one dry turn at most.",
    "compliance": "Compliance — none. Sober, precise, no irony.",
    "research": "Research — none. Sober, precise, no irony.",
    "engineering": "Engineering — none. Sober, precise, no irony.",
}
DEFAULT_TIER = REGISTER_TIER["analysis"]


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


# ── Telegram ──

def send_telegram(message: str, *, channel: str = notify.WORKLOG) -> bool:
    if not notify.telegram_allowed("syndicate.send_telegram", logger=log):
        return False
    if not TELEGRAM_BOT_TOKEN or not notify.chat_id_for(channel):
        log.warning("Telegram credentials missing")
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                       json={"chat_id": notify.chat_id_for(channel), "text": message[:4000],
                             "parse_mode": "HTML", "disable_web_page_preview": True},
                       timeout=15.0)
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


# ── Feed ──

def fetch_feed() -> list[dict]:
    """Parse the blog RSS into dicts, newest first as the feed lists them."""
    try:
        r = httpx.get(FEED_URL, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            log.warning(f"Feed HTTP {r.status_code}")
            return []
        root = ET.fromstring(r.content)
    except Exception as e:
        log.error(f"Feed fetch/parse failed: {e}")
        return []

    items = []
    for it in root.iter("item"):
        def txt(tag: str) -> str:
            el = it.find(tag)
            return (el.text or "").strip() if el is not None else ""
        link = txt("link")
        items.append({
            "guid": txt("guid") or link,
            "title": txt("title"),
            "link": link,
            "description": txt("description"),
            "category": txt("category"),
            "pub_date": txt("pubDate"),
        })
    return [i for i in items if i["link"]]


def fetch_article_text(url: str, limit: int = 6000) -> str:
    """Rough plain text of the post, to give the drafter the real argument."""
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            return ""
        body = r.text
    except Exception as e:
        log.warning(f"Article fetch failed for {url}: {e}")
        return ""
    body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", body)
    main = re.search(r"(?is)<(article|main)[^>]*>(.*?)</\1>", body)
    if main:
        body = main.group(2)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    return re.sub(r"\s+", " ", body).strip()[:limit]


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


def _system_prompt() -> str:
    docs = voice_gate.load_voice_docs()
    return (
        "You draft social copy for @moltrust in Lars Kroehl's voice.\n\n"
        "Two documents govern the writing. They are the single source of truth and "
        "they are reproduced below in full. The negative list wins over the positive "
        "model wherever they collide.\n\n"
        "=== anti-KI-Sprech.md (negative list — what must not appear) ===\n"
        f"{docs['anti_ki_sprech']}\n\n"
        "=== my-voice-en.md (positive model — how LKK builds) ===\n"
        f"{docs['my_voice_en']}\n"
    )


def _thread_instructions(item: dict, tier: str) -> str:
    return (
        f"Blog post just published:\n"
        f"Title: {item['title']}\n"
        f"Register (from the feed's own category): {item['category'] or 'Analysis'}\n"
        f"Sarcasm tier for this register (my-voice-en §0): {tier}\n"
        f"URL: {item['link']}\n"
        f"Standfirst: {item['description']}\n\n"
        f"Article text:\n{item.get('article_text', '')}\n\n"
        "Write an X thread that makes someone read the post.\n\n"
        "Hard rules:\n"
        f"- Between {THREAD_MIN} and {THREAD_MAX} tweets.\n"
        "- Each tweet at most 275 characters, counted including spaces.\n"
        "- Tweet 1 is the hook and carries NO link and NO URL of any kind. It opens "
        "on the concrete fact or scene from the post, never on MolTrust, never on "
        "'We' or 'Our'.\n"
        "- The last tweet carries the URL, exactly once, and it is the only URL in "
        "the whole thread.\n"
        "- At least one concrete number from the article appears somewhere.\n"
        "- No hashtags. No emoji. No 'thread 🧵'. No numbering like 1/5.\n"
        "- Do not build sentences as contrast pairs ('not X but Y', 'it's not X — "
        "it's Y', 'rather than X, Y'). State things directly.\n\n"
        "Then write a LinkedIn company-page post about the same article: 100-180 "
        "words, same voice, plain paragraphs, the URL on its own line at the end, "
        "no hashtags.\n\n"
        "Return strict JSON and nothing else:\n"
        '{"thread": ["tweet 1", "tweet 2", ...], "linkedin": "post text"}'
    )


def draft(item: dict) -> dict | None:
    """Ask Claude for {'thread': [...], 'linkedin': str}. None when unusable."""
    key = load_anthropic_key()
    if not key:
        log.error("No Anthropic API key available")
        return None
    tier = REGISTER_TIER.get((item.get("category") or "").strip().lower(), DEFAULT_TIER)
    client = anthropic.Anthropic(api_key=key)
    try:
        # The voice profiles run to ~25k tokens and are identical on every run,
        # so they are cached; the article text sits after them and varies.
        with client.messages.stream(
            model=MODEL_DRAFT,
            max_tokens=8000,
            system=[{"type": "text", "text": _system_prompt(),
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _thread_instructions(item, tier)}],
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.APIStatusError as e:
        log.error(f"Claude API {e.status_code}: {str(e)[:300]}")
        return None
    except anthropic.APIConnectionError as e:
        log.error(f"Claude connection failed: {e}")
        return None

    if resp.stop_reason == "refusal":
        # stop_details only exists on newer SDK builds; the server runs 0.79.0.
        details = getattr(resp, "stop_details", None)
        log.error(f"Claude refused: {getattr(details, 'category', 'unknown')}")
        return None
    if resp.stop_reason == "max_tokens":
        log.error("Draft hit max_tokens — treating as unusable")
        return None

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
    except Exception as e:
        log.error(f"Draft JSON unparseable: {e}")
        return None

    thread = [str(t).strip() for t in data.get("thread", []) if str(t).strip()]
    linkedin = str(data.get("linkedin", "")).strip()
    if not thread:
        log.error("Draft carried no thread")
        return None
    if not THREAD_MIN <= len(thread) <= THREAD_MAX:
        log.warning(f"Draft has {len(thread)} tweets, outside {THREAD_MIN}-{THREAD_MAX}")
    return {"thread": thread, "linkedin": linkedin}


# ── Bluesky ──

def bluesky_creds() -> tuple[str, str] | None:
    handle = os.getenv("BLUESKY_HANDLE", "")
    app_pw = os.getenv("BLUESKY_APP_PASSWORD", "")
    return (handle, app_pw) if handle and app_pw else None


def _facets(text: str) -> list[dict]:
    """Link facets, with byte offsets as the AT protocol wants them."""
    out = []
    raw = text.encode("utf-8")
    for m in re.finditer(rb"https?://[^\s]+", raw):
        url = m.group(0).rstrip(b".,;:)").decode("utf-8", "ignore")
        out.append({
            "index": {"byteStart": m.start(), "byteEnd": m.start() + len(url.encode("utf-8"))},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        })
    return out


def bluesky_mirror(parts: list[str]) -> list[str]:
    """Mirror the thread to Bluesky. Returns posted URIs, empty when unavailable."""
    creds = bluesky_creds()
    if not creds:
        log.info("Bluesky skipped: BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set")
        return []
    handle, app_pw = creds
    base = "https://bsky.social/xrpc"
    try:
        s = httpx.post(f"{base}/com.atproto.server.createSession",
                       json={"identifier": handle, "password": app_pw}, timeout=20)
        if s.status_code != 200:
            log.error(f"Bluesky login {s.status_code}: {s.text[:200]}")
            return []
        sess = s.json()
        headers = {"Authorization": f"Bearer {sess['accessJwt']}"}
        did = sess["did"]
    except Exception as e:
        log.error(f"Bluesky login failed: {e}")
        return []

    uris, root, parent = [], None, None
    for part in parts:
        text = part if len(part) <= BLUESKY_LIMIT else part[:BLUESKY_LIMIT - 1] + "…"
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.datetime.now(datetime.timezone.utc)
                         .isoformat().replace("+00:00", "Z"),
            "facets": _facets(text),
        }
        if root and parent:
            record["reply"] = {"root": root, "parent": parent}
        try:
            r = httpx.post(f"{base}/com.atproto.repo.createRecord", headers=headers,
                           json={"repo": did, "collection": "app.bsky.feed.post",
                                 "record": record}, timeout=20)
        except Exception as e:
            log.error(f"Bluesky post failed: {e}")
            break
        if r.status_code != 200:
            log.error(f"Bluesky createRecord {r.status_code}: {r.text[:200]}")
            break
        ref = {"uri": r.json()["uri"], "cid": r.json()["cid"]}
        uris.append(ref["uri"])
        root = root or ref
        parent = ref
    log.info(f"Bluesky: {len(uris)}/{len(parts)} posts mirrored")
    return uris


# ── Main ──

def process_item(item: dict, state: dict, dry_run: bool = False) -> bool:
    """Draft, scan, post. Returns True when the item is done with (posted or given up)."""
    guid = item["guid"]
    record = state.setdefault("items", {}).setdefault(guid, {"attempts": 0})
    record["attempts"] = record.get("attempts", 0) + 1
    record["title"] = item["title"]
    record["link"] = item["link"]

    log.info(f"New post: {item['title']} [{item['category']}] (attempt {record['attempts']})")
    item["article_text"] = fetch_article_text(item["link"])

    drafted = draft(item)
    if not drafted:
        if record["attempts"] >= MAX_DRAFT_ATTEMPTS:
            record["status"] = "draft_failed"
            send_telegram(f"⚠️ <b>Syndicate</b>\nDrafting failed "
                          f"{MAX_DRAFT_ATTEMPTS}x, giving up:\n{item['title']}\n{item['link']}")
            return True
        return False

    parts = drafted["thread"]
    scan = voice_gate.scan(parts, source_text=item.get("article_text", ""), mode="thread")
    log.info(voice_gate.format_report(scan))
    body = "\n\n".join(f"[{i}/{len(parts)}] {p}" for i, p in enumerate(parts, 1))

    if dry_run:
        print(f"\n{'=' * 60}\nDRY RUN — {item['title']} [{item['category']}]\n")
        for i, p in enumerate(parts, 1):
            print(f"[{i}/{len(parts)}] ({len(p)} chars)\n{p}\n")
        print(f"--- LinkedIn draft ---\n{drafted.get('linkedin', '(none)')}\n")
        print(voice_gate.format_report(scan))
        print(f"{'=' * 60}")
        return True

    if not scan["ok"]:
        record["status"] = "blocked"
        record["violations"] = scan["violations"]
        log.error("Pre-send scan BLOCKED the thread — not posting")
        give_up = record["attempts"] >= MAX_DRAFT_ATTEMPTS
        send_telegram(
            f"⚠️ <b>Syndicate blocked</b>{' (giving up)' if give_up else ''}\n"
            f"{html.escape(item['title'])}\n\n<pre>{html.escape(body[:1500])}</pre>\n\n"
            f"<pre>{html.escape(voice_gate.format_report(scan))}</pre>")
        return give_up

    ids = x_post.post_thread(parts)
    if not ids:
        record["status"] = "post_failed"
        send_telegram(f"⚠️ <b>Syndicate</b>\nX post failed:\n{item['title']}", channel=notify.ALERTS)
        return record["attempts"] >= MAX_DRAFT_ATTEMPTS
    if len(ids) < len(parts):
        record["status"] = "partial"
        record["tweet_ids"] = ids
        send_telegram(f"⚠️ <b>Syndicate</b>\nThread stopped at "
                      f"{len(ids)}/{len(parts)} tweets:\n{item['title']}\n"
                      f"https://x.com/MolTrust/status/{ids[0]}")
        return True

    record["status"] = "posted"
    record["tweet_ids"] = ids
    record["posted_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    thread_url = f"https://x.com/MolTrust/status/{ids[0]}"
    log.info(f"Thread posted: {thread_url}")

    record["bluesky"] = bluesky_mirror(parts)

    linkedin = drafted.get("linkedin", "")
    if linkedin:
        record["linkedin_drafted"] = True
        send_telegram(
            f"\U0001f4dd <b>LinkedIn draft</b> — paste into the MolTrust page\n"
            f"{html.escape(item['title'])}\n\n<pre>{html.escape(linkedin[:2500])}</pre>\n\n"
            f"X thread: {thread_url}\n"
            f"Bluesky: {len(record['bluesky'])} posts")
    else:
        send_telegram(f"✅ <b>Syndicate</b>\n{html.escape(item['title'])}\n"
                      f"{thread_url}\n(no LinkedIn draft returned)")

    report = os.path.join(LOG_DIR, f"syndicate_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.md")
    try:
        with open(report, "w") as f:
            f.write(f"# Syndication — {item['title']}\n\n")
            f.write(f"**Source:** {item['link']}\n**Register:** {item['category']}\n")
            f.write(f"**Thread:** {thread_url}\n")
            f.write(f"**Bluesky:** {len(record['bluesky'])} posts\n\n## Thread\n\n{body}\n\n")
            f.write(f"## LinkedIn draft\n\n{linkedin}\n\n")
            f.write(f"## Pre-send scan\n```\n{voice_gate.format_report(scan)}\n```\n")
        log.info(f"Report: {report}")
    except Exception as e:
        log.warning(f"Report write failed: {e}")
    return True


def run(dry_run: bool = False, force_guid: str | None = None) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    log.info("=" * 60)
    log.info(f"SYNDICATE — {now.strftime('%Y-%m-%d %H:%M UTC')}"
             + ("  *** DRY RUN ***" if dry_run else ""))

    items = fetch_feed()
    if not items:
        log.warning("Feed empty or unreachable")
        write_heartbeat("error", "feed empty or unreachable")
        return
    log.info(f"Feed: {len(items)} items")

    # A dry run never touches state: it drafts the newest item (or the one named
    # by --guid) and prints the result, so the gate can be exercised on demand.
    if dry_run:
        target = next((i for i in items if i["guid"] == force_guid), None) if force_guid \
            else items[0]
        if not target:
            log.error(f"No feed item with guid {force_guid}")
            return
        process_item(target, {}, dry_run=True)
        return

    state = load_state()
    if "seen" not in state:
        state["seen"] = [i["guid"] for i in items]
        state["seeded_at"] = now.isoformat()
        save_state(state)
        log.info(f"First run — seeded {len(state['seen'])} existing items, posting nothing")
        write_heartbeat("ok", f"seeded {len(state['seen'])} items")
        return

    seen = set(state["seen"])
    pending = [i for i in items if i["guid"] not in seen]
    done_items = state.get("items", {})
    pending = [i for i in pending
               if done_items.get(i["guid"], {}).get("status") not in
               ("posted", "partial", "draft_failed")]

    if not pending:
        log.info("No new posts")
        write_heartbeat("ok", "no new posts")
        return

    # Oldest first, so a burst of posts syndicates in publication order.
    for item in reversed(pending):
        try:
            finished = process_item(item, state)
        except Exception as e:
            log.error(f"Item failed: {e}\n{traceback.format_exc()}")
            finished = False
        if finished:
            state["seen"] = list(dict.fromkeys(state["seen"] + [item["guid"]]))
        save_state(state)
        break  # one post per run; the next run picks up the rest

    write_heartbeat("ok", f"{len(pending)} pending")


if __name__ == "__main__":
    try:
        guid = None
        if "--guid" in sys.argv:
            idx = sys.argv.index("--guid")
            guid = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        run(dry_run="--dry-run" in sys.argv, force_guid=guid)
    except Exception as e:
        log.error(f"FATAL: {e}\n{traceback.format_exc()}")
        write_heartbeat("crash", str(e))
        send_telegram(f"\U0001f6a8 <b>Syndicate CRASHED</b>\n<code>{str(e)[:300]}</code>", channel=notify.ALERTS)
        sys.exit(1)
