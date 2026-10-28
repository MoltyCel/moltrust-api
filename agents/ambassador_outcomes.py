"""What the ambassador's reply runs did with the posts they found, from its log.

ops AMB-NULL, 28.10.2026. From 27.10. every run logged "Found 10 posts" and
"0 replies" with every skip counter at zero, and nothing in the log said why.
Since AMB-NULL `cmd_run` writes one closing line per run,

    Outcomes: <found> posts found, <accounted> accounted for: <n> replied, ...

and the two readers here hold it to that: scripts/ambassador_outcome_check.py
(the selftest invariant) counts runs whose outcomes do not add up, and
agents/watchdog.py asks whether a day of runs saw something worth answering and
answered none of it. Standard library only, plus app.rotated_logs for the file.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

LINE_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\] (\w+): (.*)$")
START = re.compile(r"^MOLTRUST AMBASSADOR — run\b")
FOUND = re.compile(r"^Found (\d+) posts:")
OUTCOMES = re.compile(r"^Outcomes: (\d+) posts found, (\d+) accounted for: (.*)$")
COUNT = re.compile(r"(\d+) ([a-z][a-z -]*?)(?:,|$| \()")
# Outcomes that mean the post held something past every relevance filter: a
# comment or mention worth answering. Off-topic and low-effort are verdicts on
# the comment; "not considered" and "dry-run" are no verdict at all.
CANDIDATE = ("replied", "unsent", "gate", "rate-limited")


def runs(lines: list[str]) -> list[dict]:
    """One dict per `run` in log order: start, found, accounted, counts.

    found is None for a run that never got to reading the surface (blocked by
    the gate, no posts); accounted is None for a run without an Outcomes line.
    """
    out: list[dict] = []
    cur: dict | None = None
    for line in lines:
        m = LINE_RE.match(line.rstrip("\n"))
        if not m:
            continue
        ts, _level, msg = m.groups()
        if START.match(msg):
            when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            cur = {"at": when, "found": None, "accounted": None, "counts": {}}
            out.append(cur)
            continue
        if cur is None:
            continue
        f = FOUND.match(msg)
        if f:
            cur["found"] = int(f.group(1))
            continue
        o = OUTCOMES.match(msg)
        if o:
            cur["accounted"] = int(o.group(2))
            cur["counts"] = {name.strip(): int(n) for n, name in COUNT.findall(
                o.group(3).split(" (")[0])}
    return out


def in_window(all_runs: list[dict], now: datetime, hours: float) -> list[dict]:
    cut = now - timedelta(hours=hours)
    return [r for r in all_runs if r["at"] > cut]


def unaccounted(window: list[dict]) -> list[dict]:
    """Runs that found posts and did not account for every one of them."""
    return [r for r in window if r["found"] and r["accounted"] != r["found"]]


def answered_nothing(window: list[dict]) -> tuple[int, int]:
    """(candidate posts, replies) summed over the runs that wrote Outcomes."""
    cands = replies = 0
    for r in window:
        if r["accounted"] is None:
            continue
        cands += sum(r["counts"].get(k, 0) for k in CANDIDATE)
        replies += r["counts"].get("replied", 0)
    return cands, replies
