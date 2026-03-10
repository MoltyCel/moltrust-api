#!/usr/bin/env python3
"""
Moltbook Market Research Script
================================
Gathers and reports on Moltbook platform activity and engagement metrics.
Analyzes feeds, agents, submolts, and MolTrust presence.

Usage:
    python3 moltbook_research.py
    # or via venv:
    ~/moltstack/venv/bin/python3 moltbook_research.py
"""

import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://www.moltbook.com/api/v1"
SECRETS_FILE = os.path.expanduser("~/.moltrust_secrets")
REPORT_OUT = Path(__file__).resolve().parent / "moltbook_report.json"
REQUEST_TIMEOUT = 30.0
FEED_LIMIT = 50
COMMENT_LIMIT = 20
MOLTRUST_KEYWORDS = [
    "moltrust", "moltstack", "mol-trust", "mol trust",
    "trust layer", "moltrust.ch", "moltrust-agent",
]


def load_api_key():
    """Load MOLTBOOK_AGENT_KEY from ~/.moltrust_secrets."""
    if not os.path.exists(SECRETS_FILE):
        print(f"[ERROR] Secrets file not found: {SECRETS_FILE}")
        sys.exit(1)
    with open(SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "MOLTBOOK_AGENT_KEY":
                return value.strip()
    print("[ERROR] MOLTBOOK_AGENT_KEY not found in secrets file.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
async def api_get(client, path, params=None):
    """Make an authenticated GET request, return parsed JSON or error dict."""
    url = f"{BASE_URL}{path}"
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        return {"_error": True, "status": exc.response.status_code, "detail": exc.response.text[:300]}
    except httpx.RequestError as exc:
        return {"_error": True, "detail": str(exc)[:300]}


async def fetch_feed(client, sort, limit=FEED_LIMIT):
    """Fetch a feed of posts sorted by sort (hot | new)."""
    data = await api_get(client, "/posts", {"sort": sort, "limit": limit})
    if data.get("_error"):
        print(f"  [WARN] Failed to fetch {sort} feed: {data}")
        return []
    return data.get("posts", [])


async def fetch_submolts(client):
    """Fetch all submolts."""
    data = await api_get(client, "/submolts")
    if data.get("_error"):
        print(f"  [WARN] Failed to fetch submolts: {data}")
        return []
    return data.get("submolts", [])


async def fetch_comments(client, post_id, limit=COMMENT_LIMIT):
    """Fetch top comments for a post."""
    data = await api_get(client, f"/posts/{post_id}/comments", {"limit": limit})
    if data.get("_error"):
        return []
    return data.get("comments", [])


async def fetch_own_agent(client):
    """Fetch our own agent profile via /agents/me."""
    data = await api_get(client, "/agents/me")
    if data.get("_error"):
        return None
    return data.get("agent")


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------
def post_summary(post):
    """Extract a clean summary dict from a raw post."""
    author = post.get("author") or {}
    submolt = post.get("submolt") or {}
    return {
        "title": (post.get("title") or "")[:100],
        "author": author.get("name", "?"),
        "author_karma": author.get("karma", 0),
        "submolt": submolt.get("name", "general"),
        "score": post.get("score", 0),
        "upvotes": post.get("upvotes", 0),
        "downvotes": post.get("downvotes", 0),
        "comment_count": post.get("comment_count", 0),
        "created_at": post.get("created_at", ""),
    }


def analyse_agents_from_posts(posts):
    """Build agent activity stats from a list of posts."""
    agents = {}
    for p in posts:
        author = p.get("author") or {}
        name = author.get("name")
        if not name:
            continue
        if name not in agents:
            agents[name] = {
                "name": name,
                "karma": author.get("karma", 0),
                "followers": author.get("followerCount", 0),
                "post_count": 0,
                "total_score": 0,
                "total_comments_received": 0,
            }
        agents[name]["post_count"] += 1
        agents[name]["total_score"] += p.get("score", 0)
        agents[name]["total_comments_received"] += p.get("comment_count", 0)
    return sorted(agents.values(), key=lambda a: a["total_score"], reverse=True)


def analyse_commenters(all_comments):
    """Find the most active commenters from sampled comments."""
    counter = Counter()
    karma_map = {}
    for c in all_comments:
        author = c.get("author") or {}
        name = author.get("name")
        if name:
            counter[name] += 1
            karma_map[name] = author.get("karma", 0)
    return [
        {"name": name, "comments_sampled": count, "karma": karma_map.get(name, 0)}
        for name, count in counter.most_common(15)
    ]


def check_moltrust_presence(posts, comments):
    """Search posts and comments for MolTrust-related content."""
    matches = []
    for p in posts:
        title_str = p.get("title", "")
        content_str = p.get("content", "")
        text = f"{title_str} {content_str}".lower()
        author = (p.get("author") or {}).get("name", "").lower()
        if any(kw in text or kw in author for kw in MOLTRUST_KEYWORDS):
            matches.append({
                "type": "post",
                "title": (p.get("title") or "")[:80],
                "author": (p.get("author") or {}).get("name", "?"),
                "score": p.get("score", 0),
            })
    for c in comments:
        text = (c.get("content") or "").lower()
        author = (c.get("author") or {}).get("name", "").lower()
        if any(kw in text or kw in author for kw in MOLTRUST_KEYWORDS):
            matches.append({
                "type": "comment",
                "content": (c.get("content") or "")[:80],
                "author": (c.get("author") or {}).get("name", "?"),
                "score": c.get("score", 0),
            })
    return {"keyword_matches": len(matches), "items": matches[:20]}


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
SEPARATOR = "=" * 72
THIN_SEP = "-" * 72


def print_header(title):
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def print_post_table(posts, label):
    print_header(f"{label} (top {len(posts)})")
    print("  {:<4} {:>7} {:>8} {:<22} {}".format("#", "Score", "Cmts", "Author", "Title"))
    print(f"  {THIN_SEP}")
    for i, p in enumerate(posts, 1):
        s = post_summary(p)
        title_trunc = s["title"][:50]
        print("  {:<4} {:>7,} {:>8,} {:<22} {}".format(i, s["score"], s["comment_count"], s["author"], title_trunc))


def print_submolts(submolts):
    print_header("Submolt Activity")
    sorted_s = sorted(submolts, key=lambda s: s.get("post_count", 0), reverse=True)
    print("  {:<25} {:>10} {:>14}".format("Submolt", "Posts", "Subscribers"))
    print(f"  {THIN_SEP}")
    for s in sorted_s:
        print("  {:<25} {:>10,} {:>14,}".format(
            s.get("display_name", "?"), s.get("post_count", 0), s.get("subscriber_count", 0)))


def print_agents(agents, label):
    print_header(label)
    print("  {:<22} {:>8} {:>6} {:>11} {:>10}".format("Agent", "Karma", "Posts", "TotalScore", "CmtsRecvd"))
    print(f"  {THIN_SEP}")
    for a in agents[:15]:
        print("  {:<22} {:>8,} {:>6} {:>11,} {:>10,}".format(
            a["name"], a["karma"], a["post_count"], a["total_score"], a["total_comments_received"]))


def print_commenters(commenters):
    print_header("Most Active Commenters (sampled)")
    print("  {:<25} {:>10} {:>10}".format("Agent", "Comments", "Karma"))
    print(f"  {THIN_SEP}")
    for c in commenters:
        print("  {:<25} {:>10} {:>10,}".format(c["name"], c["comments_sampled"], c["karma"]))


def print_engagement(hot, new):
    print_header("Engagement Metrics")
    for label, posts in [("Hot Feed", hot), ("New Feed", new)]:
        if not posts:
            continue
        scores = [p.get("score", 0) for p in posts]
        comments = [p.get("comment_count", 0) for p in posts]
        avg_score = sum(scores) / len(scores)
        avg_comments = sum(comments) / len(comments)
        median_score = sorted(scores)[len(scores) // 2]
        max_score = max(scores)
        print(f"\n  [{label} - {len(posts)} posts]")
        print("    Avg score:       {:>10,.1f}".format(avg_score))
        print("    Median score:    {:>10,}".format(median_score))
        print("    Max score:       {:>10,}".format(max_score))
        print("    Avg comments:    {:>10,.1f}".format(avg_comments))
        print("    Total comments:  {:>10,}".format(sum(comments)))


def print_moltrust(presence, own_agent):
    print_header("MolTrust Presence on Moltbook")
    if own_agent:
        print("  Our Agent:       {}".format(own_agent.get("name", "?")))
        print("  Display Name:    {}".format(own_agent.get("display_name", "?")))
        print("  Karma:           {}".format(own_agent.get("karma", 0)))
        print("  Followers:       {}".format(own_agent.get("follower_count", 0)))
        print("  Posts:           {}".format(own_agent.get("posts_count", 0)))
        print("  Comments:        {}".format(own_agent.get("comments_count", 0)))
        print("  Verified:        {}".format(own_agent.get("is_verified", False)))
        print("  Created:         {}".format(own_agent.get("created_at", "?")))
    else:
        print("  [Could not fetch own agent profile]")

    print("\n  Keyword matches in feeds: {}".format(presence.get("keyword_matches", 0)))
    for item in presence.get("items", []):
        kind = item["type"].upper()
        desc = item.get("title") or item.get("content", "")
        print("    [{}] by {} (score {}): {}".format(kind, item["author"], item["score"], desc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    api_key = load_api_key()
    headers = {"Authorization": f"Bearer {api_key}"}
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print(SEPARATOR)
    print("  MOLTBOOK MARKET RESEARCH REPORT")
    print("  Generated: {}".format(timestamp))
    print(SEPARATOR)

    async with httpx.AsyncClient(headers=headers, timeout=REQUEST_TIMEOUT) as client:
        # 1. Fetch feeds and submolts concurrently
        hot_posts, new_posts, submolts, own_agent = await asyncio.gather(
            fetch_feed(client, "hot", FEED_LIMIT),
            fetch_feed(client, "new", FEED_LIMIT),
            fetch_submolts(client),
            fetch_own_agent(client),
        )

        # 2. Platform Stats
        stats_data = await api_get(client, "/posts", {"sort": "new", "limit": 1})
        total_posts_count = int(stats_data.get("count", 0)) if not stats_data.get("_error") else None

        print_header("Platform Overview")
        if total_posts_count is not None:
            print("  Total posts on platform:  {:>12,}".format(total_posts_count))
        total_submolt_posts = sum(s.get("post_count", 0) for s in submolts) if submolts else 0
        print("  Total submolts:           {:>12,}".format(len(submolts)))
        print("  Total submolt posts:      {:>12,}".format(total_submolt_posts))

        # Unique agents seen in both feeds
        all_posts = hot_posts + new_posts
        unique_authors = set()
        for p in all_posts:
            author = p.get("author") or {}
            name = author.get("name")
            if name:
                unique_authors.add(name)
        print("  Unique agents in feeds:   {:>12,}".format(len(unique_authors)))

        # Active agents
        active_agents = set()
        for p in all_posts:
            author = p.get("author") or {}
            if author.get("isActive"):
                active_agents.add(author.get("name"))
        print("  Active agents in feeds:   {:>12,}".format(len(active_agents)))

        # 3. Hot Feed
        print_post_table(hot_posts[:10], "Hot Feed")

        # 4. New Feed
        print_post_table(new_posts[:10], "New Feed")

        # 5. Active Agents
        agent_stats = analyse_agents_from_posts(all_posts)
        print_agents(agent_stats, "Most Active Agents (by total score in feeds)")

        # 6. Sample comments from top hot posts
        print("\n  [Sampling comments from top posts...]")
        all_comments = []
        comment_tasks = [
            fetch_comments(client, p["id"], COMMENT_LIMIT)
            for p in hot_posts[:5] if p.get("id")
        ]
        comment_results = await asyncio.gather(*comment_tasks)
        for comments in comment_results:
            all_comments.extend(comments)

        commenters = analyse_commenters(all_comments)
        print_commenters(commenters)

        # 7. Submolts
        print_submolts(submolts)

        # 8. Engagement Metrics
        print_engagement(hot_posts, new_posts)

        # 9. MolTrust Presence
        presence = check_moltrust_presence(all_posts, all_comments)
        print_moltrust(presence, own_agent)

        # --------------- Build JSON report ---------------
        report = {
            "generated_at": timestamp,
            "platform": {
                "total_posts": total_posts_count,
                "total_submolts": len(submolts),
                "total_submolt_posts": total_submolt_posts,
                "unique_agents_in_feeds": len(unique_authors),
                "active_agents_in_feeds": len(active_agents),
            },
            "hot_feed": [post_summary(p) for p in hot_posts[:10]],
            "new_feed": [post_summary(p) for p in new_posts[:10]],
            "top_agents": agent_stats[:15],
            "top_commenters": commenters,
            "submolts": [
                {
                    "name": s.get("display_name", s.get("name", "?")),
                    "post_count": s.get("post_count", 0),
                    "subscriber_count": s.get("subscriber_count", 0),
                }
                for s in sorted(submolts, key=lambda x: x.get("post_count", 0), reverse=True)
            ],
            "engagement": {},
            "moltrust_presence": {
                "own_agent": own_agent,
                "keyword_matches": presence.get("keyword_matches", 0),
                "items": presence.get("items", []),
            },
        }
        for label, posts in [("hot_feed", hot_posts), ("new_feed", new_posts)]:
            if posts:
                scores = [p.get("score", 0) for p in posts]
                comments = [p.get("comment_count", 0) for p in posts]
                report["engagement"][label] = {
                    "count": len(posts),
                    "avg_score": round(sum(scores) / len(scores), 1),
                    "median_score": sorted(scores)[len(scores) // 2],
                    "max_score": max(scores),
                    "avg_comments": round(sum(comments) / len(comments), 1),
                    "total_comments": sum(comments),
                }

        # Save JSON
        with open(REPORT_OUT, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print("\n{}".format(SEPARATOR))
        print("  JSON report saved to: {}".format(REPORT_OUT))
        print(SEPARATOR)


if __name__ == "__main__":
    asyncio.run(main())
