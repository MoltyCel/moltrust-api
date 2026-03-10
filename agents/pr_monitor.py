"""MolTrust PR Monitor — Watches open PRs/Issues and sends Telegram alerts on changes."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ── Config ──────────────────────────────────────────────────────────────────

STATE_FILE = Path(os.path.expanduser("~/moltstack/data/pr_monitor_state.json"))
LOG_DIR = Path(os.path.expanduser("~/moltstack/logs"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

GITHUB_API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github.v3+json", "User-Agent": "MolTrust-PR-Monitor/1.0"}

MOLTBOOK_API = "https://www.moltbook.com/api/v1"
MOLTBOOK_KEY = os.getenv("MOLTBOOK_AGENT_KEY", "")
MOLTBOOK_HEADERS = {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}

# Moltbook submolts to monitor: (name, short_name)
TRACKED_SUBMOLTS = [
    ("agenttrust", "m/agenttrust"),
]

# PRs and Issues to monitor: (owner, repo, number, short_name, type)
TRACKED = [
    ("punkpeye", "awesome-mcp-servers", 2227, "awesome-mcp", "pr"),
    ("modelcontextprotocol", "servers", 3394, "mcp/servers", "pr"),
    ("BankrBot", "openclaw-skills", 175, "openclaw", "pr"),
    ("chatmcp", "mcpso", 551, "mcp.so", "issue"),
    ("animo", "awesome-self-sovereign-identity", 55, "awesome-ssi", "pr"),
    ("aarora4", "Awesome-Prediction-Market-Tools", 10, "pred-markets", "pr"),
    ("appcypher", "awesome-mcp-servers", 442, "appcypher-mcp", "pr"),
    ("yzfly", "Awesome-MCP-ZH", 41, "awesome-mcp-zh", "pr"),
    ("sudeepb02", "awesome-erc8004", 16, "awesome-erc8004", "pr"),
    ("Puliczek", "awesome-mcp-security", 46, "mcp-security", "pr"),
    ("TensorBlock", "awesome-mcp-servers", 116, "tensorblock", "pr"),
    ("YuzeHao2023", "Awesome-MCP-Servers", 32, "yuzehao-mcp", "pr"),
    ("erc-8004", "erc-8004-contracts", 59, "erc8004-contracts", "issue"),
    ("google-agentic-commerce", "a2a-x402", 67, "a2a-x402", "issue"),
    ("Agentic-Trust-Layer", "agentic-trust", 1, "agentic-trust", "issue"),
    ("camel-ai", "agent-trust", 3, "camel-trust", "issue"),
    ("zCloak-Network", "ATP", 2, "zcloak-atp", "issue"),
    ("0xperp", "awesome-prediction-markets", 3, "pred-markets-1", "pr"),
    ("buddies2705", "awesome-prediction-market", 1, "pred-markets-2", "pr"),
    ("Solsory", "x402-erc8004-agent", 1, "solsory-x402", "issue"),
    ("AgentlyHQ", "aixyz", 212, "aixyz", "issue"),
    ("ChaosChain", "trustless-agents-erc-ri", 13, "chaoschain-erc", "issue"),
    ("murrlincoln", "anet", 2, "anet", "issue"),
    ("OnChainMee", "x402-erc8004-agent", 2, "onchainmee-x402", "issue"),
]

# ── GitHub fetching ─────────────────────────────────────────────────────────


def fetch_pr_or_issue(owner: str, repo: str, number: int, kind: str) -> dict | None:
    """Fetch a single PR or issue from GitHub API. Falls back to /issues/ if /pulls/ fails."""
    if kind == "pr":
        url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}"
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=15.0, follow_redirects=True)
            if resp.status_code == 200:
                return resp.json()
            print(f"  /pulls/ returned {resp.status_code}, trying /issues/ fallback...")
        except httpx.HTTPError as e:
            print(f"  /pulls/ error: {e}, trying /issues/ fallback...")

    # Use /issues/ endpoint (works for both PRs and issues)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}"
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15.0, follow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            # For PRs via /issues/, derive merged from pull_request.merged_at
            if kind == "pr" and "pull_request" in data:
                data["merged"] = bool(data["pull_request"].get("merged_at"))
            return data
        print(f"  Warning: {url} returned {resp.status_code}")
        return None
    except httpx.HTTPError as e:
        print(f"  Error fetching {url}: {e}")
        return None


def fetch_comments(owner: str, repo: str, number: int, since: str | None = None) -> list:
    """Fetch comments on a PR/issue, optionally since a timestamp."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments"
    params = {}
    if since:
        params["since"] = since
    try:
        resp = httpx.get(url, headers=HEADERS, params=params, timeout=15.0, follow_redirects=True)
        if resp.status_code == 200:
            return resp.json()
        return []
    except httpx.HTTPError:
        return []


def fetch_reviews(owner: str, repo: str, number: int) -> list:
    """Fetch PR reviews."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/reviews"
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15.0, follow_redirects=True)
        if resp.status_code == 200:
            return resp.json()
        return []
    except httpx.HTTPError:
        return []


# ── State management ────────────────────────────────────────────────────────


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Telegram ────────────────────────────────────────────────────────────────


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
        }, timeout=15.0)
        if resp.status_code == 200:
            return True
        print(f"  Telegram error: {resp.status_code} {resp.text[:200]}")
        return False
    except httpx.HTTPError as e:
        print(f"  Telegram error: {e}")
        return False


# ── Monitor logic ───────────────────────────────────────────────────────────

STATUS_EMOJI = {
    "open": "\u23f3",       # ⏳
    "merged": "\u2705",     # ✅
    "closed": "\u274c",     # ❌
}


def check_all() -> tuple[list[dict], list[str]]:
    """Check all tracked PRs/issues. Returns (results, changes)."""
    state = load_state()
    results = []
    changes = []
    now = datetime.now(timezone.utc).isoformat()

    for owner, repo, number, short_name, kind in TRACKED:
        key = f"{owner}/{repo}#{number}"
        prev = state.get(key, {})
        print(f"Checking {key} ...")

        data = fetch_pr_or_issue(owner, repo, number, kind)
        if data is None:
            results.append({"key": key, "short_name": short_name, "status": "error", "error": "fetch failed"})
            continue

        # Determine status
        if kind == "pr" and data.get("merged"):
            status = "merged"
        elif data.get("state") == "closed":
            status = "closed"
        else:
            status = "open"

        labels = [l["name"] for l in data.get("labels", [])]

        # Fetch comments since last check
        last_checked = prev.get("last_checked")
        comments = fetch_comments(owner, repo, number, since=last_checked)
        # Filter out our own comments (MoltyCel)
        new_comments = [c for c in comments if c.get("user", {}).get("login") != "MoltyCel"]

        # Fetch reviews for PRs
        new_reviews = []
        if kind == "pr":
            reviews = fetch_reviews(owner, repo, number)
            prev_review_ids = set(prev.get("review_ids", []))
            new_reviews = [r for r in reviews if r["id"] not in prev_review_ids]
            review_ids = [r["id"] for r in reviews]
        else:
            review_ids = []

        # Build result
        result = {
            "key": key,
            "short_name": short_name,
            "status": status,
            "title": data.get("title", ""),
            "labels": labels,
            "new_comments": len(new_comments),
            "comment_authors": list({c.get("user", {}).get("login", "?") for c in new_comments}),
            "new_reviews": len(new_reviews),
            "review_authors": list({r.get("user", {}).get("login", "?") for r in new_reviews}),
            "updated_at": data.get("updated_at", ""),
        }
        results.append(result)

        # Detect changes
        prev_status = prev.get("status")
        item_changes = []

        if prev_status and prev_status != status:
            emoji = STATUS_EMOJI.get(status, "?")
            item_changes.append(f"{emoji} #{number} {short_name} — {status}!")
        elif new_comments:
            authors = ", ".join(f"@{a}" for a in result["comment_authors"])
            item_changes.append(f"\U0001f4ac #{number} {short_name} — new comment from {authors}")
        elif new_reviews:
            authors = ", ".join(f"@{a}" for a in result["review_authors"])
            item_changes.append(f"\U0001f50d #{number} {short_name} — new review from {authors}")
        elif labels != prev.get("labels", []):
            item_changes.append(f"\U0001f3f7 #{number} {short_name} — labels changed: {', '.join(labels) or 'none'}")

        changes.extend(item_changes)

        # Update state
        state[key] = {
            "status": status,
            "labels": labels,
            "comment_count": data.get("comments", 0),
            "review_ids": review_ids,
            "last_checked": now,
        }

        # Rate-limit: 60 req/h unauthenticated → be gentle
        time.sleep(1)

    # ── Moltbook submolts ──
    for submolt_name, short_name in TRACKED_SUBMOLTS:
        key = f"moltbook/{submolt_name}"
        prev = state.get(key, {})
        print(f"Checking {key} ...")

        try:
            resp = httpx.get(
                f"{MOLTBOOK_API}/submolts/{submolt_name}",
                headers=MOLTBOOK_HEADERS, timeout=15.0,
            )
            if resp.status_code != 200:
                print(f"  Moltbook returned {resp.status_code}")
                results.append({"key": key, "short_name": short_name, "status": "error", "error": "fetch failed"})
                continue
            data = resp.json().get("submolt", {})
        except httpx.HTTPError as e:
            print(f"  Moltbook error: {e}")
            results.append({"key": key, "short_name": short_name, "status": "error", "error": str(e)})
            continue

        subs = data.get("subscriber_count", 0)
        posts = data.get("post_count", 0)
        prev_subs = prev.get("subscriber_count", 0)
        prev_posts = prev.get("post_count", 0)

        result = {
            "key": key,
            "short_name": short_name,
            "status": "active",
            "subscriber_count": subs,
            "post_count": posts,
            "new_comments": 0,
            "comment_authors": [],
            "new_reviews": 0,
            "review_authors": [],
            "labels": [],
        }
        results.append(result)

        item_changes = []
        if prev_subs and subs != prev_subs:
            diff = subs - prev_subs
            item_changes.append(f"\U0001f4e2 {short_name}: {diff:+d} subscribers (now {subs})")
        if prev_posts and posts != prev_posts:
            diff = posts - prev_posts
            item_changes.append(f"\U0001f4dd {short_name}: {diff:+d} new posts (now {posts})")
        changes.extend(item_changes)

        state[key] = {
            "subscriber_count": subs,
            "post_count": posts,
            "last_checked": now,
        }

    state["_last_run"] = now
    save_state(state)
    return results, changes


def format_report(results: list, changes: list) -> str:
    """Format a human-readable report."""
    lines = ["\U0001f4ca <b>PR-Monitor Report</b>", "\u2501" * 20]
    for r in results:
        if r.get("error"):
            tag = r['key'].split('#')[1] if '#' in r['key'] else r['key']
            lines.append(f"\u26a0\ufe0f {tag} {r['short_name']} — error")
            continue
        if r["key"].startswith("moltbook/"):
            subs = r.get("subscriber_count", 0)
            posts = r.get("post_count", 0)
            lines.append(f"\U0001f9e0 {r['short_name']} — {subs} subs, {posts} posts")
            continue
        emoji = STATUS_EMOJI.get(r["status"], "?")
        detail = r["status"]
        if r["new_comments"]:
            authors = ", ".join(f"@{a}" for a in r["comment_authors"])
            detail += f", {r['new_comments']} new comment(s) from {authors}"
        elif r["new_reviews"]:
            authors = ", ".join(f"@{a}" for a in r["review_authors"])
            detail += f", review from {authors}"
        if r["labels"]:
            detail += f" [{', '.join(r['labels'])}]"
        lines.append(f"{emoji} #{r['key'].split('#')[1]} {r['short_name']} — {detail}")
    return "\n".join(lines)


def format_changes(changes: list) -> str:
    """Format only the changes for Telegram notification."""
    lines = ["\U0001f4ca <b>PR-Monitor Update</b>", "\u2501" * 20]
    lines.extend(changes)
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    mode = sys.argv[1] if len(sys.argv) > 1 else "check"

    if mode == "check":
        results, changes = check_all()
        report = format_report(results, changes)
        print(report)

        # Save JSON report
        report_file = STATE_FILE.parent / "pr_monitor_report.json"
        report_file.write_text(json.dumps(results, indent=2))
        print(f"\nJSON report saved to {report_file}")

        # Send Telegram only if there are changes
        if changes:
            msg = format_changes(changes)
            if send_telegram(msg):
                print("Telegram notification sent.")
            else:
                print("Telegram notification failed.")
        else:
            print("No changes detected — no notification sent.")

    elif mode == "report":
        # Force send full report via Telegram regardless of changes
        results, changes = check_all()
        report = format_report(results, changes)
        print(report)
        if send_telegram(report):
            print("Full report sent via Telegram.")
        else:
            print("Telegram send failed.")

    elif mode == "status":
        # Just print current state
        state = load_state()
        if state:
            last = state.get("_last_run", "never")
            print(f"Last run: {last}")
            for key, val in state.items():
                if key.startswith("_"):
                    continue
                print(f"  {key}: {val.get('status', '?')}")
        else:
            print("No state file found. Run 'check' first.")

    else:
        print(f"Usage: {sys.argv[0]} [check|report|status]")
        sys.exit(1)


if __name__ == "__main__":
    main()
