"""Read app/sql/public_count.sql and hand back what it measured.

Three callers need the public number: the milestone trigger, the
registry-proof export, and whatever reports it next. Each of them parsing psql
output on its own is how two reports end up answering two questions under one
word, so the parsing lives here once.

The file is the definition; this module only runs it and reads the blocks back.
Nothing here recomputes a figure, and a run whose completeness probes fail
raises instead of returning a number.
"""
from __future__ import annotations

import os
import subprocess

SQL_PATH = os.path.join(os.path.dirname(__file__), "sql", "public_count.sql")


class IncompleteRead(RuntimeError):
    """public_count.sql ran but could not prove it saw everything."""


def _psql(path: str, timeout: int = 180) -> str:
    out = subprocess.run(
        ["psql", "-h", "localhost", "-U", "moltstack", "-d", "moltstack",
         "-X", "-A", "-F", "\t", "-P", "pager=off", "-f", path],
        capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError(f"psql: {out.stderr[:400]}")
    return out.stdout


def _section(text: str, title: str) -> list[list[str]]:
    """Rows of one '== title ==' block, its header line dropped."""
    rows, grab = [], False
    for line in text.splitlines():
        if line.startswith("== "):
            grab = line.strip().strip("= ").strip() == title
            continue
        if grab and line.strip() and not line.startswith("("):
            rows.append(line.split("\t"))
    return rows[1:] if rows else []


def read(sql_path: str | None = None) -> dict:
    """Run the file and return its blocks. Raises if completeness fails.

    Keys: counts, buckets, per_day, rate7, scripted, checks.
    """
    raw = _psql(os.path.abspath(sql_path or SQL_PATH))

    head = _section(raw, "the two headline figures")
    buckets = _section(raw, "public count, by bucket")
    per_day = _section(raw, "registrations per day, last 14")
    rate = _section(raw, "rate7 over the counted buckets")
    checks = _section(raw, "completeness")
    if not (head and buckets and rate and checks):
        raise IncompleteRead("public_count.sql returned an unexpected shape")

    covers_all, scripted_intact, scripted_unquoted = (c == "t" for c in checks[0][:3])
    if not (covers_all and scripted_intact and scripted_unquoted):
        raise IncompleteRead(
            f"completeness failed (buckets={covers_all} "
            f"scripted_intact={scripted_intact} unquoted={scripted_unquoted})")

    public, activated, unmeasured, deducted, all_live = (int(x) for x in head[0])
    by_bucket = [{"bucket": b, "agents": int(a), "anchored": int(an),
                  "activated": int(ac), "unmeasured": int(um)}
                 for b, a, an, ac, um in buckets]
    counted = next((b for b in by_bucket if b["bucket"] == "bounty"), None)

    return {
        "counts": {
            "public": public,
            "activated": activated,
            "unmeasured": unmeasured,
            "deducted": deducted,
            "all_live": all_live,
            "bounty": counted["anchored"] if counted else 0,
        },
        "buckets": by_bucket,
        "per_day": [{"day": d, "registered": int(n)} for d, n in per_day],
        "rate7": float(rate[0][0]),
        "log_window_starts": checks[0][3] if len(checks[0]) > 3 else None,
    }
