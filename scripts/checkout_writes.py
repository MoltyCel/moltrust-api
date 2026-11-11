"""Hands in the deployed checkouts, from the reflog, for the 08:00 report.

The deployed checkouts (~/moltstack, ~/moltrust-web) belong to the deploy
(WORKFLOW 11.7, 2026-10-10). Since then deploy.sh tags every git step with
GIT_REFLOG_ACTION="deploy.sh <repo> <sha>". A fetch, checkout, merge or reset
in those checkouts whose reflog entry lacks that tag came from someone else.
On 2026-10-09 such a fetch, at 18:08:09, made the deploy of #705 fail.

Entries before the first tagged one are not judged: until deploy.sh tagged,
its own entries looked the same as anyone's.

refs/remotes/origin/main is shared by every worktree of the checkout (one
.git, 44 worktrees on 2026-10-10), so a fetch from a worktree shows up here
as well. logs/HEAD is the checkout's own HEAD; worktrees keep theirs apart.
"""
from __future__ import annotations

import datetime as dt
import os

CHECKOUTS = {"moltrust-api": "~/moltstack", "moltrust-web": "~/moltrust-web"}
REFLOGS = ("logs/HEAD", "logs/refs/remotes/origin/main", "logs/refs/heads/main")
TAG = "deploy.sh "
WINDOW_HOURS = 24
MAX_ITEMS = 6


def read_reflog(path: str) -> list[tuple[dt.datetime, str, str, str]]:
    """(time, old, new, message) per line."""
    out = []
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return out
    with fh:
        for line in fh:
            head, _, msg = line.rstrip("\n").partition("\t")
            parts = head.split()
            if len(parts) < 4:
                continue
            try:
                ts = dt.datetime.fromtimestamp(int(parts[-2]), dt.timezone.utc)
            except ValueError:
                continue
            out.append((ts, parts[0], parts[1], msg))
    return out


def foreign_entries(gitdir: str, now: dt.datetime) -> list[tuple[str, dt.datetime, str]]:
    since = now - dt.timedelta(hours=WINDOW_HOURS)
    found = []
    for rel in REFLOGS:
        rows = read_reflog(os.path.join(gitdir, rel))
        tagged = [t for t, _, _, m in rows if m.startswith(TAG)]
        if not tagged:
            continue                    # tagging not live in this checkout yet
        start = max(min(tagged), since)
        for t, old, new, m in rows:
            if t >= start and t < now and not m.startswith(TAG):
                found.append((rel.replace("logs/", ""), t, m))
    return sorted(found, key=lambda x: x[1])


def lines(now: dt.datetime | None = None, checkouts: dict | None = None) -> list[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    checkouts = checkouts or CHECKOUTS
    heads, items = [], []
    for name, path in checkouts.items():
        gitdir = os.path.join(os.path.expanduser(path), ".git")
        if not os.path.isdir(gitdir):
            heads.append(f"{name} ?")
            items.append(f"  {name}: kein Git-Verzeichnis unter {path}")
            continue
        f = foreign_entries(gitdir, now)
        heads.append(f"{name} {len(f)}")
        items += [f"  {name} {ref} {t:%d.%m. %H:%M:%S}Z: {m[:80]}" for ref, t, m in f[:MAX_ITEMS]]
    return [f"Handgriffe im Checkout (Reflog ohne deploy.sh, {WINDOW_HOURS} h) — {', '.join(heads)}"] + items


if __name__ == "__main__":
    print("\n".join(lines()))
