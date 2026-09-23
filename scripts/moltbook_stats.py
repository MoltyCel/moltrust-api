#!/usr/bin/env python3
"""What Moltbook brought in, and what Moltbook thinks of us.

    python3 scripts/moltbook_stats.py            # human-readable
    python3 scripts/moltbook_stats.py --telegram # one line to the stats chat

Two figures, and they belong together. Registrations with platform='moltbook'
say whether being there brings anyone in. The share of our comments the
platform marks as spam says what it costs us to be there. Reporting the first
without the second is how a channel keeps its budget while its reputation
falls.

The spam share is read live from the Moltbook API rather than from our own
records, because it is their judgement of us and we do not hold it. The
default window is the newest 100 comments, which is what the 91 % figure was
quoted against and what one call returns; --full walks the whole history for a
lifetime rate and takes 128 pages to do it.

Exit 0 on success, 1 when a figure could not be established. Read-only: this
writes nothing to Moltbook.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import datetime as _dt

BASE = "https://www.moltbook.com/api/v1"

# Reply-only mode went on at 2026-09-23 11:30 UTC. No comment has been deleted,
# deliberately: the marked share is the measurement, and deleting it would
# remove the thing that has to move before anyone can say the rebuild worked.
#
# The question is whether Moltbook's judgement follows behaviour or sticks to
# the account. It is decided on the reading from 2026-09-30, and the rule is
# written here rather than remembered, so the answer cannot be chosen after
# seeing the number.
DECISION_DATE = _dt.date(2026, 9, 30)
DECISION_THRESHOLD = 50.0
PRIMARY_ACCOUNT = "moltrust-agent"
MAX_PAGES = 200          # 100 rows a page; refuses rather than guessing
RECENT = 100             # the window the 91 % figure was quoted against
SECRETS = os.path.expanduser("~/.moltrust_secrets")

# Both accounts. moltguard_v1 is the control: same platform, 2.6 % marked
# against moltrust-agent's 76.8 %, which is what made it clear the problem was
# the comment engine rather than Moltbook's tolerance for us.
ACCOUNTS = ("MOLTBOOK_AGENT_KEY", "MOLTBOOK_API_KEY_MOLTGUARD")


def secret(name: str) -> str:
    try:
        for line in open(SECRETS, encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.environ.get(name, "")


def api(path: str, key: str, **params):
    url = f"{BASE}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}", "User-Agent": "moltrust-stats/1.0"})
    delay = 2.0
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310 - BASE is https and constant
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 5:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
    raise RuntimeError("unreachable")


def recent_comments(key: str) -> list[dict]:
    """The newest 100 comments, in one call.

    /agents/me/comments returns newest first — verified against the ordering,
    not assumed — so the window the 91 % figure was quoted against is the first
    page and needs no paging. A weekly job that walked 128 pages for the same
    headline would grow one page longer every two days.
    """
    body = api("/agents/me/comments", key, limit=RECENT)
    rows = body.get("comments") or []
    stamps = [c.get("created_at") or "" for c in rows]
    if stamps != sorted(stamps, reverse=True):
        raise RuntimeError("comments are no longer newest-first; the recent window is wrong")
    return rows


def all_comments(key: str, declared: int) -> list[dict]:
    """Every comment on the account, or an exception.

    Two pagination shapes live in this API: /agents/me/comments sends
    has_more, /agents/me/posts sends only next_cursor. Stopping on a missing
    has_more read 100 of 668 posts once and called it the total, so presence
    of a cursor counts as well.
    """
    out: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        body = api("/agents/me/comments", key, **params)
        rows = body.get("comments") or []
        out.extend(rows)
        nxt = body.get("next_cursor")
        more = body.get("has_more") if "has_more" in body else bool(nxt)
        if not more or not rows:
            break
        if not nxt or nxt == cursor:
            raise RuntimeError("more pages announced but the cursor does not advance")
        cursor = nxt
        time.sleep(1.2)
    else:
        raise RuntimeError(f"more than {MAX_PAGES} pages, no complete history")

    seen = {c.get("id") for c in out}
    if len(seen) != declared:
        raise RuntimeError(
            f"read {len(seen)} comments, the profile reports {declared} "
            f"(difference {declared - len(seen):+d}); not reporting a rate from that")
    return out


def account(key_name: str, full: bool) -> dict:
    key = secret(key_name)
    if not key:
        raise RuntimeError(f"{key_name} not set")
    me = api("/agents/me", key)["agent"]
    recent = recent_comments(key)
    out = {
        "name": me["name"],
        "karma": me["karma"],
        "comments": me["comments_count"],
        "recent_n": len(recent),
        "spam_recent": sum(1 for c in recent if c.get("is_spam")),
        "spam_total": None,
    }
    if full:
        every = all_comments(key, me["comments_count"])
        out["spam_total"] = sum(1 for c in every if c.get("is_spam"))
    return out


def registrations(dsn: str) -> tuple[int, int]:
    import psycopg2  # imported late: the Moltbook half works without a database

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agents WHERE platform = 'moltbook'")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM agents WHERE platform = 'moltbook' "
                    "  AND created_at >= NOW() - INTERVAL '7 days'")
        week = cur.fetchone()[0]
    return total, week


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="also walk the whole history for a lifetime rate "
                         "(128 pages and growing; not for the weekly run)")
    ap.add_argument("--dsn", default=os.environ.get(
        "DATABASE_URL", "postgresql://moltstack@localhost/moltstack"))
    args = ap.parse_args()

    try:
        accounts = [account(name, args.full) for name in ACCOUNTS]
        total, week = registrations(args.dsn)
    except Exception as exc:  # noqa: BLE001 - the message is the output
        print(f"moltbook_stats: {exc}", file=sys.stderr)
        return 1

    def pct(part: int, whole: int) -> float:
        return (part / whole * 100) if whole else 0.0

    def verdict() -> str | None:
        """What the reading means, once the date to read it has come."""
        if _dt.date.today() < DECISION_DATE:
            days = (DECISION_DATE - _dt.date.today()).days
            return f"Entscheidung am {DECISION_DATE:%d.%m.}, noch {days} Tage, bis dahin keine Löschung"
        primary = next((a for a in accounts if a["name"] == PRIMARY_ACCOUNT), None)
        if primary is None or not primary["recent_n"]:
            return None
        share = pct(primary["spam_recent"], primary["recent_n"])
        if share < DECISION_THRESHOLD:
            return (f"letzte {primary['recent_n']} bei {share:.0f} % — unter {DECISION_THRESHOLD:.0f} %, "
                    f"die Markierung folgt dem Verhalten. Löschfrage neu vorlegen.")
        return (f"letzte {primary['recent_n']} bei {share:.0f} % — nicht unter {DECISION_THRESHOLD:.0f} %, "
                f"die Markierung klebt am Konto. 500er-Löschung testen.")

    if args.telegram:
        parts = [f"{a['name']} {pct(a['spam_recent'], a['recent_n']):.0f}%"
                 for a in accounts]
        line = (f"Moltbook: {week} Registrierungen (7d), {total} gesamt · "
                f"Spam-Quote letzte {RECENT}: " + ", ".join(parts))
        said = verdict()
        if said:
            line += " · " + said
        print(line)
        return 0

    print(f"Registrierungen platform=moltbook: {total} gesamt, {week} in 7 Tagen")
    for a in accounts:
        line = (f"  {a['name']:<16} Karma {a['karma']:>5}  "
                f"Kommentare {a['comments']:>6}  "
                f"letzte {a['recent_n']}: {a['spam_recent']} Spam "
                f"({pct(a['spam_recent'], a['recent_n']):.0f} %)")
        if a["spam_total"] is not None:
            line += (f"  gesamt {a['spam_total']} "
                     f"({pct(a['spam_total'], a['comments']):.1f} %)")
        print(line)
    said = verdict()
    if said:
        print(f"  {said}")
    # Deliberately absent: a post count. /agents/me/posts serves 517 where the
    # profile says 668, with no gap at the old end and no parameter that
    # exposes the difference — include_deleted, deleted, status and
    # include_removed are all accepted and ignored. Until that is answered,
    # any post figure from this platform would be a number we cannot stand
    # behind. See CLAUDE.md.
    return 0


if __name__ == "__main__":
    sys.exit(main())
