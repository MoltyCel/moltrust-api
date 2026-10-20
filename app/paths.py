"""Where the server's state lives, resolved when it is asked for.

Every data file and log file sits under one root. Until 2026-10-04 each module
computed its own path from `os.path.expanduser("~/moltstack/data")` **at import
time**, which has one consequence that matters: nothing can redirect it
afterwards. A test that sets a different directory, a worktree that should
write beside itself, a dry run that should touch nothing — all of them keep
writing to the live files.

That is not hypothetical. On 2026-10-04 a test run against a worktree wrote
three fixture rows into `data/linkedin_metrics.jsonl`, the production series,
because `append(row, path=SERIES)` bound SERIES when the module loaded. The
same gap reaches `x_meter.jsonl`, `digest_metrics.jsonl` and
`video_posts.jsonl` — the ledgers decisions are made from. `conftest.py` has
carried the same lesson for the database since 2026-06-17, when ~20 rows
leaked into the live audit table.

So: one function, read at call time.

    from app import paths
    paths.data("x_meter.jsonl")     ~/moltstack/data/x_meter.jsonl
    paths.logs("watchdog.log")      ~/moltstack/logs/watchdog.log

`MOLTRUST_ROOT` overrides the root, and it is read on every call — not cached,
not bound to a default argument. The test suite sets it to a temporary
directory, and `tests/conftest.py` additionally refuses any write under the
production data and log trees, so a module that has not been migrated yet
fails loudly instead of quietly writing where it should not.
"""
from __future__ import annotations

import os

ENV = "MOLTRUST_ROOT"
DEFAULT = "~/moltstack"


def root() -> str:
    """The state root. Environment first, read every time it is asked."""
    return os.path.abspath(os.path.expanduser(os.environ.get(ENV) or DEFAULT))


def under(*parts: str) -> str:
    return os.path.join(root(), *parts)


def data(*parts: str) -> str:
    return under("data", *parts)


def logs(*parts: str) -> str:
    return under("logs", *parts)


def ensure(path: str) -> str:
    """Make the parent directory and return the path, for a caller about to write."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def is_production(path: str) -> bool:
    """Would writing here touch the live data or log trees?

    Compared against the default root and not against `root()`: the question
    is whether a write lands in production, and when MOLTROOT points
    somewhere else that is exactly what we want to know.
    """
    real = os.path.abspath(os.path.expanduser(DEFAULT))
    target = os.path.abspath(path)
    return any(target == os.path.join(real, sub)
               or target.startswith(os.path.join(real, sub) + os.sep)
               for sub in ("data", "logs"))
