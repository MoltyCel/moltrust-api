"""MolTrust News Scout — Daily RSS/news scan for MolTrust-relevant developments."""

import json
import os
import re
import sys
import time
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import httpx

# ── Config ──────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.path.expanduser("~/moltstack/data"))
LOG_DIR = Path(os.path.expanduser("~/moltstack/logs"))
CACHE_FILE = DATA_DIR / "news_sent_urls.json"
HEARTBEAT_FILE = DATA_DIR / "news_scout_heartbeat.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

USER_AGENT = "MolTrust-NewsScout/1.0"
MAX_ITEMS = 12
LOOKBACK_HOURS = 48

# ── Topics ──────────────────────────────────────────────────────────────────

TOPICS = [
    {
        "emoji": "🏛️",
        "name": "Regulation",
        "keywords": [
            "agentic AI governance", "IMDA", "EU AI Act agents",
            "AI agent framework", "autonomous agents compliance",
        ],
        "feeds": [
            "https://news.ycombinator.com/rss",
            "https://techcrunch.com/feed/",
        ],
    },
    {
        "emoji": "🤖",
        "name": "Agent Economy",
        "keywords": [
            "AI agent identity", "agent trust", "A2A protocol",
            "agent authorization", "x402", "MCP agent",
        ],
        "feeds": [
            "https://news.ycombinator.com/rss",
            "https://techcrunch.com/feed/",
            "https://www.theblock.co/rss.xml",
        ],
    },
    {
        "emoji": "🎯",
        "name": "Competitors",
        "keywords": [
            "AstraSync", "Know Your Agent", "agent registry",
            "agent identity platform",
        ],
        "feeds": [],  # Google News only
    },
    {
        "emoji": "📄",
        "name": "Research",
        "keywords": [
            "agent trust", "agent identity", "verifiable credentials agent",
            "W3C DID agent", "agent authorization",
        ],
        "feeds": [
            "https://rss.arxiv.org/rss/cs.AI",
            "https://rss.arxiv.org/rss/cs.CR",
        ],
    },
]

# ── Helpers ─────────────────────────────────────────────────────────────────


def load_cache() -> set:
    if CACHE_FILE.exists():
        try:
            return set(json.loads(CACHE_FILE.read_text()))
        except (json.JSONDecodeError, TypeError):
            return set()
    return set()


def save_cache(urls: set):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Keep last 500 URLs to avoid unbounded growth
    trimmed = sorted(urls)[-500:]
    CACHE_FILE.write_text(json.dumps(trimmed, indent=2))


def save_heartbeat(status: str, detail: str = ""):
    HEARTBEAT_FILE.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "detail": detail,
    }, indent=2))


def url_key(url: str) -> str:
    """Normalize URL for dedup."""
    return hashlib.md5(url.strip().lower().encode()).hexdigest()


def parse_date(date_str: str) -> datetime | None:
    """Best-effort parse of RSS date strings."""
    if not date_str:
        return None
    # Strip timezone abbreviations like "EST", "GMT"
    cleaned = re.sub(r'\s+[A-Z]{2,4}$', '', date_str.strip())
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(cleaned, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ── Feed fetching ───────────────────────────────────────────────────────────


def fetch_rss(url: str, timeout: float = 15.0) -> list[dict]:
    """Fetch and parse an RSS/Atom feed. Returns list of {title, link, published, summary}."""
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
        if resp.status_code != 200:
            print(f"  RSS {resp.status_code}: {url}")
            return []
    except httpx.HTTPError as e:
        print(f"  RSS error: {url} — {e}")
        return []

    items = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        print(f"  RSS parse error: {url}")
        return []

    # Handle RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate") or item.findtext("dc:date") or ""
        desc = (item.findtext("description") or "").strip()
        items.append({"title": title, "link": link, "published": pub, "summary": desc})

    # Handle Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link[@rel='alternate']", ns) or entry.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        pub = entry.findtext("atom:published", namespaces=ns) or entry.findtext("atom:updated", namespaces=ns) or ""
        desc = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
        items.append({"title": title, "link": link, "published": pub, "summary": desc})

    return items


def fetch_google_news(keyword: str) -> list[dict]:
    """Fetch Google News RSS for a keyword."""
    url = f"https://news.google.com/rss/search?q={quote_plus(keyword)}&hl=en&gl=US&ceid=US:en"
    return fetch_rss(url)


# ── Scoring ─────────────────────────────────────────────────────────────────


def score_item(item: dict, keywords: list[str]) -> int:
    """Score an item's relevance. Higher = more relevant."""
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    score = 0
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in text:
            # Exact phrase match in title = high
            if kw_lower in item.get("title", "").lower():
                score += 10
            else:
                score += 5
        else:
            # Partial: check if all words present
            words = kw_lower.split()
            if len(words) > 1 and all(w in text for w in words):
                score += 3
    return score


def strip_html(text: str) -> str:
    """Remove HTML tags from string."""
    return re.sub(r'<[^>]+>', '', text).strip()


def truncate(text: str, length: int = 100) -> str:
    text = strip_html(text).replace('\n', ' ').strip()
    if len(text) <= length:
        return text
    return text[:length].rsplit(' ', 1)[0] + "…"


# ── Main logic ──────────────────────────────────────────────────────────────


def scan_all() -> dict[str, list[dict]]:
    """Scan all topics. Returns {topic_name: [scored items]}."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    sent = load_cache()
    seen_urls = set()  # dedup within run
    results = {}

    for topic in TOPICS:
        topic_items = []
        print(f"\n{'='*40}\n{topic['emoji']} {topic['name']}")

        # Fetch configured RSS feeds
        feed_items = []
        for feed_url in topic["feeds"]:
            print(f"  Fetching {feed_url[:60]}...")
            feed_items.extend(fetch_rss(feed_url))
            time.sleep(1)

        # Fetch Google News for each keyword
        for kw in topic["keywords"]:
            print(f"  Google News: {kw}")
            feed_items.extend(fetch_google_news(kw))
            time.sleep(1.5)  # be gentle

        print(f"  Raw items: {len(feed_items)}")

        # Filter + score
        for item in feed_items:
            link = item.get("link", "").strip()
            if not link:
                continue

            uk = url_key(link)
            if uk in seen_urls or uk in sent:
                continue

            # Date filter
            pub_dt = parse_date(item.get("published", ""))
            if pub_dt and pub_dt < cutoff:
                continue

            score = score_item(item, topic["keywords"])
            if score < 3:
                continue

            seen_urls.add(uk)
            item["_score"] = score
            item["_topic"] = topic["name"]
            topic_items.append(item)

        # Sort by score desc
        topic_items.sort(key=lambda x: x["_score"], reverse=True)
        print(f"  Matched: {len(topic_items)}")
        if topic_items:
            results[topic["name"]] = topic_items

    return results


def format_telegram(results: dict[str, list[dict]]) -> str:
    """Format results as Telegram message."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"📡 <b>MolTrust News Scout — {today}</b>", ""]

    total = sum(len(v) for v in results.values())
    if total < 3:
        lines.append("Quiet day — fewer than 3 relevant items found.")
        if results:
            lines.append("")

    topic_map = {t["name"]: t["emoji"] for t in TOPICS}
    budget = MAX_ITEMS
    for topic_name, items in results.items():
        if budget <= 0:
            break
        emoji = topic_map.get(topic_name, "📌")
        lines.append(f"{emoji} <b>{topic_name}</b>")
        for item in items[:min(4, budget)]:
            title = strip_html(item.get("title", "Untitled"))[:120]
            summary = truncate(item.get("summary", ""), 90)
            link = item.get("link", "")
            lines.append(f"• <a href=\"{link}\">{title}</a>")
            if summary:
                lines.append(f"  {summary}")
            budget -= 1
        lines.append("")

    if total == 0:
        lines.append("No relevant items in the last 48h.")

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured, printing instead:")
        print(message)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = httpx.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15.0)
        if resp.status_code == 200:
            return True
        print(f"  Telegram error: {resp.status_code} {resp.text[:200]}")
        return False
    except httpx.HTTPError as e:
        print(f"  Telegram error: {e}")
        return False


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    test_mode = "--test" in sys.argv

    print(f"News Scout {'[TEST]' if test_mode else '[LIVE]'} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    try:
        results = scan_all()
        msg = format_telegram(results)

        print("\n" + "=" * 40)
        print(msg)
        print("=" * 40)

        total = sum(len(v) for v in results.values())
        print(f"\nTotal items: {total}")

        if test_mode:
            print("\n[TEST] Telegram message NOT sent. Use without --test to send.")
        else:
            if send_telegram(msg):
                print("Telegram digest sent.")
            else:
                print("Telegram send failed.")

            # Update cache with sent URLs
            sent = load_cache()
            for items in results.values():
                for item in items[:MAX_ITEMS]:
                    sent.add(url_key(item.get("link", "")))
            save_cache(sent)

        save_heartbeat("ok", f"{total} items")

    except Exception as e:
        save_heartbeat("error", str(e)[:200])
        raise


if __name__ == "__main__":
    main()
