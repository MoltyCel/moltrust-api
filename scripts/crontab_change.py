#!/usr/bin/env python3
"""Every change to the live crontab, with who, when and a copy before and after.

On 2026-10-18 a line appeared in the live crontab and the server could not say
who had put it there (ops CJ9). The writer had made a backup and reported the
change, but the backup carried no name and the report lived somewhere the
server never looks. A line nobody can trace from the server is the class of the
hand edits of 12.10.2026 even when its author meant well, so the server keeps
the record itself (ops KQ2).

One log, appended to by every writer: ~/crontab-backups/changes.jsonl. Each
entry names who and why, the time, the copy taken before and the copy read
back after, and their sha256. scripts/check_crontab_snapshot.py names the last
entry when it finds a difference, and says so when the live crontab is not the
one the last entry left behind.

    python scripts/crontab_change.py --who "Koordinator" --why "CJ6: board line" new.txt
    python scripts/crontab_change.py --who "Console" --why "baseline" --note

--note records the live crontab without changing it, for a first entry or for
a change that was made some other way and is being owned up to. A script that
changes the crontab itself calls apply() (scripts/x_spend_after_radar.py does).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys

BACKUPS = "~/crontab-backups"
LOG_NAME = "changes.jsonl"


def _dir() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser(BACKUPS))


def log_path() -> pathlib.Path:
    return _dir() / LOG_NAME


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def read_live(run=None) -> tuple[str | None, str | None]:
    """(text, None) or (None, why)."""
    run = run or subprocess.run
    try:
        got = run(["crontab", "-l"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"crontab -l: {type(exc).__name__}: {exc}"
    if got.returncode != 0:
        return None, f"crontab -l exit {got.returncode}"
    return got.stdout, None


def _counts(before: str, after: str) -> tuple[int, int]:
    b, a = before.splitlines(), after.splitlines()
    return len(set(a) - set(b)), len(set(b) - set(a))


def apply(new_text: str | None, who: str, why: str, run=None,
          now: datetime.datetime | None = None) -> tuple[int, dict | None, str | None]:
    """Install new_text as the live crontab (None: record only) and log it.

    Returns (rc, entry, why_not). rc 0: done and read back as written. rc 1:
    the install failed or the crontab read back differs; logged all the same,
    because the crontab may have changed. rc 2: nothing was changed and nothing
    logged (no name, no reason, or the live crontab could not be read).
    """
    run = run or subprocess.run
    if not who.strip() or not why.strip():
        return 2, None, "--who and --why are both required"
    before, err = read_live(run)
    if before is None:
        return 2, None, err
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    before_file = d / f"crontab-{stamp}-before.txt"
    before_file.write_text(before)

    result = "noted, unchanged"
    if new_text is not None:
        put = run(["crontab", "-"], input=new_text, capture_output=True,
                  text=True, timeout=60)
        result = "ok" if put.returncode == 0 else f"crontab - exit {put.returncode}"
    after, err = read_live(run)
    if after is None:
        after, result = "", f"{result}; read back failed: {err}"
    elif new_text is not None and result == "ok" and after != new_text:
        result = "read back differs from the candidate"
    after_file = d / f"crontab-{stamp}-after.txt"
    after_file.write_text(after)

    added, removed = _counts(before, after)
    entry = {"ts": now.isoformat(timespec="seconds"), "who": who.strip(),
             "why": why.strip(), "result": result,
             "before": str(before_file), "after": str(after_file),
             "before_sha256": sha256(before), "after_sha256": sha256(after),
             "lines_before": len(before.splitlines()),
             "lines_after": len(after.splitlines()),
             "added": added, "removed": removed,
             "user": os.environ.get("USER", ""), "sid": os.getsid(0)}
    with open(log_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    ok = result in ("ok", "noted, unchanged")
    return (0 if ok else 1), entry, (None if ok else result)


def last() -> dict | None:
    """The newest entry of the log, or None if there is none."""
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def describe(entry: dict | None) -> str:
    if entry is None:
        return f"last logged change: none ({log_path()} is empty or missing)"
    return (f"last logged change: {entry.get('ts')} by {entry.get('who')}: "
            f"{entry.get('why')} ({entry.get('result')}, +{entry.get('added')}"
            f"/-{entry.get('removed')}; before {entry.get('before')}, "
            f"after {entry.get('after')})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--who", required=True)
    ap.add_argument("--why", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("candidate", nargs="?")
    g.add_argument("--note", action="store_true")
    a = ap.parse_args(argv)
    new_text = None if a.note else pathlib.Path(a.candidate).read_text()
    rc, entry, why_not = apply(new_text, a.who, a.why)
    if entry:
        print(describe(entry))
    if why_not:
        print(f"NOT DONE: {why_not}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
