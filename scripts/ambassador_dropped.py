#!/usr/bin/env python3
"""The daily report's block on conversations the ambassador gave up on.

    python3 scripts/ambassador_dropped.py              last 12 hours, plain text
    python3 scripts/ambassador_dropped.py --html       escaped for Telegram HTML
    python3 scripts/ambassador_dropped.py --hours 24

agents/ambassador.py writes one line to ~/moltstack/data/ambassador_dropped.jsonl
when the gate refused three drafts and the short fallback without figures as
well. Until 24.10.2026 such a comment was marked seen and nothing else happened:
between 22.10 and 24.10 three of five comments ended that way, and the only
trace was a log line nobody reads. The block always prints, "keine" included, so
its absence from a delivered report is itself visible.

Standard library only: scripts/daily_stats.sh runs it under the system python3.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import sys
from pathlib import Path

DEFAULT_FILE = Path.home() / "moltstack" / "data" / "ambassador_dropped.jsonl"
HEAD = "Ambassador, aufgegebene Gespräche"


def entries(path: Path, since: datetime.datetime) -> list[dict]:
    """Dropped conversations at or after `since`, oldest first."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
            at = datetime.datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc)
        except (ValueError, KeyError, TypeError):
            continue
        if at >= since:
            out.append(e)
    return out


def block(rows: list[dict], hours: float) -> list[str]:
    span = f"{hours:g} h"
    if not rows:
        return [f"{HEAD} ({span}): keine"]
    lines = [f"{HEAD} ({span}): {len(rows)}"]
    for e in rows:
        title = (e.get("post_title") or "")[:40]
        lines.append(f"  {e.get('at', '')[11:16]}Z {e.get('author', '?')} zu „{title}“ "
                     f"({(e.get('comment_id') or '')[:8]})")
        lines.append(f"    abgelehnt: {(e.get('refusal') or '')[:120]}")
        lines.append(f"    Rückfall: {(e.get('fallback') or '')[:120]}")
    return lines


def main(argv: list[str] | None = None,
         now: datetime.datetime | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--html", action="store_true")
    args = ap.parse_args(argv)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rows = entries(args.file, now - datetime.timedelta(hours=args.hours))
    text = "\n".join(block(rows, args.hours))
    print(html.escape(text, quote=False) if args.html else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
