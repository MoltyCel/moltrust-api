#!/usr/bin/env python3
"""
MolTrust Discovery — finds new relevant GitHub threads daily.
Runs at 06:00 UTC. Telegram-review-only: writes a sidecar
candidates file, never touches MoltyCel's live watch_list.json.

Migrated from VCOne-Box (178.104.48.73) on 2026-05-10.
- Sidecar JSON instead of live watchlist
- Secrets from environment (loaded by cron from ~/.moltrust_secrets)
- No MEMORY.md logging (VCOne-specific)
- Stale-prune at 14d (Telegram-buffer, not historical record)
"""
import os
import sys
import json
import time
import datetime
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# --- Configuration ---
# GITHUB_PAT was a classic ghp_ token written 2026-05-13. Classic tokens
# default to 90 days, so it died on 2026-08-11 — the exact day every search in
# this log started returning 401, and it kept returning 401 unnoticed for a
# month because a failed run looks identical to a quiet one.
#
# GH_TOKEN is the maintained project token and answers the same search. Either
# is accepted so a future rotation of GITHUB_PAT takes effect without a code
# change; whichever is present is checked before the run starts.
GITHUB_PAT = os.environ.get("GITHUB_PAT", "") or os.environ.get("GH_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_DIR = Path("/home/moltstack/moltycelbot")
CANDIDATES_FILE = BASE_DIR / "discovery_candidates.json"
LOG_DIR = Path("/home/moltstack/logs")
LOG_FILE = LOG_DIR / "discovery.log"
HEALTH_FILE = BASE_DIR / "discovery_health.json"

# Alert on the third consecutive failure, then every seventh, so a persistent
# outage stays visible without turning into daily noise.
FAILURES_BEFORE_ALERT = 3
ALERT_REPEAT_EVERY = 7

if not GITHUB_PAT:
    sys.stderr.write("FATAL: GITHUB_PAT not set in environment\n")
    sys.exit(1)

HEADERS = {
    "Authorization": f"token {GITHUB_PAT}",
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "MolTrust-Discovery/1.0",
}

QUERIES = [
    "agent identity verification W3C DID",
    "agent trust infrastructure verifiable credentials",
    "x402 payment agent authorization",
    "A2A agent-to-agent trust",
    "AI agent identity authorization AAE",
    "did:moltrust OR MolTrust agent",
]

SKIP_REPOS = [
    "MoltyCel", "status.moltrust", "moltrust-mcp",
    "moltrust-protocol", "moltrust-api", "moltrust-sdk",
    "moltrust-x402", "moltrust-openclaw", "moltrust-verify",
]

SPAM_INDICATORS = ["\u8d4c", "\u8718\u86db", "\u767e\u5ea6", "\u5907\u7528", "\u7f51\u8d4c", "\u7f51\u5740"]

STALE_DAYS = 14
MAX_NEW = 20


def moltycell_already_commented(repo, number):
    url = f"https://api.github.com/repos/{repo}/issues/{number}/comments?per_page=100"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            comments = json.loads(resp.read())
            return any(
                c.get("user", {}).get("login", "").lower() == "moltycel"
                for c in comments
            )
    except Exception:
        return False


def load_candidates():
    if CANDIDATES_FILE.exists():
        try:
            return json.loads(CANDIDATES_FILE.read_text())
        except Exception:
            pass
    return {"candidates": []}


def save_candidates(data):
    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_FILE.write_text(json.dumps(data, indent=2))


def load_health():
    try:
        return json.loads(HEALTH_FILE.read_text())
    except Exception:
        return {"consecutive_failures": 0, "last_ok": None}


def save_health(data):
    try:
        HEALTH_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"  Could not write {HEALTH_FILE.name}: {e}")


def token_is_live():
    """Check the credential before spending the run on it.

    A dead token turns every search into a caught exception and the run into a
    cheerful "0 new, 0 pruned". Asking once, up front, is what makes the
    difference between a quiet day and a broken one visible.
    """
    req = urllib.request.Request("https://api.github.com/user", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        detail = "credential rejected" if e.code == 401 else f"HTTP {e.code}"
        return False, detail
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def record_failure(reason):
    """Count a failed run and alert once it stops looking like a blip."""
    health = load_health()
    health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
    health["last_error"] = reason
    n = health["consecutive_failures"]
    save_health(health)

    print(f"MolTrust Discovery FAILED ({n} in a row): {reason}")

    should_alert = n == FAILURES_BEFORE_ALERT or (
        n > FAILURES_BEFORE_ALERT and (n - FAILURES_BEFORE_ALERT) % ALERT_REPEAT_EVERY == 0
    )
    if should_alert:
        last_ok = health.get("last_ok") or "unknown"
        send_telegram(
            f"\u26a0\ufe0f MolTrust Discovery has failed {n} runs in a row.\n"
            f"Reason: {reason}\n"
            f"Last successful run: {last_ok}\n"
            f"Log: {LOG_FILE}"
        )
    return n


def record_success(today):
    health = load_health()
    previous = int(health.get("consecutive_failures", 0))
    save_health({"consecutive_failures": 0, "last_ok": today, "last_error": None})
    if previous >= FAILURES_BEFORE_ALERT:
        send_telegram(f"\u2705 MolTrust Discovery is running again after {previous} failed runs.")


def search_github(query):
    params = urllib.parse.urlencode({
        "q": f"{query} is:open is:issue",
        "sort": "updated",
        "order": "desc",
        "per_page": 5,
    })
    url = f"https://api.github.com/search/issues?{params}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("items", [])
    except Exception as e:
        print(f"  Search error for '{query}': {e}")
        return []


def discover_threads():
    found = []
    for query in QUERIES:
        print(f"  Searching: {query}")
        items = search_github(query)
        for item in items:
            repo = item["repository_url"].replace(
                "https://api.github.com/repos/", ""
            )
            if any(skip in repo for skip in SKIP_REPOS):
                continue
            title = item.get("title", "")
            if any(s in title for s in SPAM_INDICATORS):
                continue
            found.append({
                "repo": repo,
                "number": item["number"],
                "title": item["title"][:120],
                "url": item["html_url"],
                "updated_at": item["updated_at"],
                "query": query,
            })
        time.sleep(2)

    seen = set()
    unique = []
    for item in found:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)

    filtered = []
    for thread in unique:
        if moltycell_already_commented(thread["repo"], thread["number"]):
            print(f"  SKIP (MoltyCel present): {thread['repo']}#{thread['number']}")
            continue
        filtered.append(thread)
        time.sleep(1)

    return filtered[:MAX_NEW]


def send_telegram(msg):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("  Telegram not configured — skipping send")
        return
    try:
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  Telegram error: {e}")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    print(f"MolTrust Discovery — {today}")

    live, detail = token_is_live()
    if not live:
        record_failure(f"GitHub credential unusable ({detail})")
        sys.exit(1)

    state = load_candidates()
    existing_urls = {c["url"] for c in state["candidates"]}

    discovered = discover_threads()
    new = [t for t in discovered if t["url"] not in existing_urls]

    for t in new:
        state["candidates"].append({
            "repo": t["repo"],
            "number": t["number"],
            "title": t["title"],
            "url": t["url"],
            "added_at": today,
            "query": t.get("query", ""),
        })
        print(f"  NEW: {t['repo']}#{t['number']} — {t['title'][:60]}")

    cutoff = (
        datetime.date.today() - datetime.timedelta(days=STALE_DAYS)
    ).isoformat()
    before = len(state["candidates"])
    state["candidates"] = [
        c for c in state["candidates"]
        if c.get("added_at", today) > cutoff
    ]
    pruned = before - len(state["candidates"])

    save_candidates(state)

    summary = (
        f"MolTrust Discovery: {len(new)} new, "
        f"{pruned} pruned, {len(state['candidates'])} total"
    )
    print(summary)
    record_success(today)

    if new:
        msg = f"\U0001f50d {summary}\n\n"
        for t in new[:5]:
            msg += f"\u2022 {t['repo']}#{t['number']}\n  {t['title'][:60]}\n  {t['url']}\n"
        if len(new) > 5:
            msg += f"\n+ {len(new) - 5} weitere in {CANDIDATES_FILE.name}"
        send_telegram(msg)
    else:
        print("No new threads — no Telegram sent")

    with open(LOG_FILE, "a") as f:
        f.write(
            f"{datetime.datetime.now(datetime.timezone.utc).isoformat()} "
            f"\u2014 {summary}\n"
        )


if __name__ == "__main__":
    main()
