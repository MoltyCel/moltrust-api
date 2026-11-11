"""Read a log file together with the files logrotate moved it to.

A check that reads only `name.log` sees an empty file right after a rotation and
takes it for an agent that stopped. On 2026-10-23 at 16:07 UTC the logs were
rotated outside the usual slot, and two readers fell for it:

- the watchdog's ReplyRadar check read the tail of `reply_radar.log`, found no
  run header in it and reported "Job lief nicht" every hour from 17:00 until the
  radar's next run at 06:05;
- the daily report read `moltbook.log` through scripts/moltbook_verify_rate.py
  and said "keine Daten" at 07:00 on 24.10, although the poster had verified a
  post every day.

logrotate on the server writes `name.log-YYYYMMDD`, compressed or not
(`dateext`). The numbered form `name.log.1` is accepted too, so a changed
logrotate config does not bring the false alarm back. Standard library only:
moltbook_verify_rate.py runs under the system python3, not the venv.
"""
from __future__ import annotations

import gzip
import os
import re


def rotated_siblings(path: str) -> list[str]:
    """The rotated copies of `path`, newest first, by modification time."""
    folder, base = os.path.split(os.fspath(path))
    pattern = re.compile(re.escape(base) + r"(?:-\d{8}|\.\d+)(?:\.gz)?$")
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return []
    found = [os.path.join(folder, n) for n in names if pattern.fullmatch(n)]
    return sorted(found, key=os.path.getmtime, reverse=True)


def _read(path: str) -> list[str]:
    if path.endswith(".gz"):
        with gzip.open(path, "rt", errors="replace") as fh:
            return fh.read().splitlines()
    with open(path, errors="replace") as fh:
        return fh.read().splitlines()


def tail_lines(path: str, n: int) -> list[str]:
    """The last `n` lines of the log, reaching into rotated copies if needed.

    The current file comes last. Older copies are prepended, newest first, only
    while fewer than `n` lines are at hand. Raises OSError when neither the file
    nor any rotated copy exists, so a missing log stays a finding.
    """
    lines: list[str] = []
    found = False
    if os.path.exists(path):
        lines = _read(path)
        found = True
    for older in rotated_siblings(path):
        if len(lines) >= n:
            break
        lines = _read(older) + lines
        found = True
    if not found:
        raise FileNotFoundError(path)
    return lines[-n:]


def read_with_previous(path: str, previous: int = 1) -> list[str]:
    """The current log preceded by its `previous` newest rotated copies.

    For readers that count over the whole file rather than a tail: the count no
    longer drops to zero at every rotation. Raises OSError as tail_lines does.
    """
    lines: list[str] = []
    found = False
    for older in reversed(rotated_siblings(path)[:previous]):
        lines += _read(older)
        found = True
    if os.path.exists(path):
        lines += _read(path)
        found = True
    if not found:
        raise FileNotFoundError(path)
    return lines
