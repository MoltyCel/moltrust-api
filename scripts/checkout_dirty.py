"""Uncommitted changes in the deployed checkouts, for c-checkout-clean (hourly).

The deployed checkouts belong to the deploy (CLAUDE.md "Ein Schreiber, und der
ist der Deploy"; WORKFLOW 11.7). On 2026-10-10 two files in ~/moltstack were
edited by hand from 09:16 UTC; nothing reported it until deploy.sh refused
the deploy of #718 at 09:19. agents/supervision.py checks the same thing, but
it runs from the GitHub schedule, which fired 2 of 13 hourly slots when last
measured. This runs on the server, from the hourly selftest.

Per changed path it prints file, lines (+/-), age (last modification) and the
time it was first seen. First seen is kept in ~/selftest/checkout-dirty-seen.json;
a path that is clean again drops out, and comes back as new.

Untracked files count too, except those listed in
config/checkout_untracked_baseline.json: they stood there when this check went
live and are a finding of their own, not a new hand.

Output: one line per finding, the single line "CHECKOUT SAUBER" when there is none.
The notify throttle (app/notify.py) holds a repeated identical report to once
an hour; ages and timestamps are volatile there, so a growing age is the same
report.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

CHECKOUTS = {"moltrust-api": "~/moltstack", "moltrust-web": "~/moltrust-web"}
SEEN = "~/selftest/checkout-dirty-seen.json"
BASELINE = os.path.join(os.path.dirname(__file__), "..", "config",
                        "checkout_untracked_baseline.json")


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          check=True, timeout=60).stdout


def changes(repo: str) -> list[tuple[str, str, str]]:
    """(status, path, lines) for every uncommitted path; untracked included."""
    out = []
    numstat = {}
    for ln in _git(repo, "diff", "--numstat", "HEAD").splitlines():
        a, d, p = ln.split("\t", 2)
        numstat[p] = f"+{a}/-{d}" if a != "-" else "binaer"
    for ln in _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=normal").split("\0"):
        if not ln:
            continue
        st, p = ln[:2], ln[3:]
        if st == "??":
            full = os.path.join(repo, p)
            if os.path.isdir(full):
                lines = "Verzeichnis"
            else:
                try:
                    with open(full, "rb") as fh:
                        lines = f"+{sum(1 for _ in fh)}"
                except OSError:
                    lines = "?"
            out.append(("??", p.rstrip("/"), lines))
        else:
            out.append((st.strip(), p, numstat.get(p, "?")))
    return out


def _mtime(path: str) -> dt.datetime | None:
    try:
        if os.path.isdir(path):
            ts = max((os.stat(os.path.join(r, f)).st_mtime
                      for r, _, fs in os.walk(path) for f in fs), default=os.stat(path).st_mtime)
        else:
            ts = os.stat(path).st_mtime
    except OSError:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


def _age(now: dt.datetime, t: dt.datetime | None) -> str:
    if t is None:
        return "?"
    m = int((now - t).total_seconds() // 60)
    return f"{m} min" if m < 120 else f"{m // 60} h" if m < 2880 else f"{m // 1440} d"


def report(now: dt.datetime | None = None, checkouts: dict | None = None,
           seen_path: str = SEEN, baseline_path: str = BASELINE) -> list[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    checkouts = checkouts or CHECKOUTS
    seen_path = os.path.expanduser(seen_path)
    try:
        with open(seen_path) as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    try:
        with open(baseline_path) as fh:
            baseline = json.load(fh).get("untracked", {})
    except (OSError, ValueError):
        baseline = {}
    lines, still = [], {}
    for name, path in checkouts.items():
        repo = os.path.expanduser(path)
        if not os.path.isdir(os.path.join(repo, ".git")):
            lines.append(f"UNREADABLE {name}: kein Git-Verzeichnis unter {path}")
            continue
        try:
            found = changes(repo)
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            lines.append(f"UNREADABLE {name}: git status: {type(exc).__name__}")
            continue
        for st, p, n in found:
            if st == "??" and p in baseline.get(name, []):
                continue
            key = f"{name}:{p}"
            first = seen.get(key) or now.isoformat(timespec="seconds")
            still[key] = first
            t = _mtime(os.path.join(repo, p))
            lines.append(f"{name} {st} {p} {n}, geaendert vor {_age(now, t)}"
                         f" ({t:%d.%m. %H:%M}Z), zuerst gesehen {first[:16]}Z"
                         if t else f"{name} {st} {p} {n}, zuerst gesehen {first[:16]}Z")
    try:
        os.makedirs(os.path.dirname(seen_path), exist_ok=True)
        tmp = seen_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(still, fh, indent=1, sort_keys=True)
        os.replace(tmp, seen_path)
    except OSError as exc:
        lines.append(f"UNREADABLE Beobachtungsdatei nicht schreibbar: {type(exc).__name__}")
    return lines or ["CHECKOUT SAUBER"]


if __name__ == "__main__":
    out = report()
    print("\n".join(out))
    sys.exit(0)
