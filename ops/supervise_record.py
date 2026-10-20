"""The supervisor's own bookkeeping: heartbeat and one line per run.

Separate from scripts/selftest.py and agents/supervision.py because those are
read-only and must stay that way — a diagnostic that writes is a diagnostic
that can change what it measures. The supervisor is allowed to write; it is
recording that it ran, not deciding anything.

    ops/supervise_record.py stamp <origin>                 # heartbeat, first
    ops/supervise_record.py history <json-file> <origin>   # one row, after

`origin` says where the run came from, and it is an argument rather than an
environment variable because the deploy key carries a forced command and no
environment crosses that boundary. Four shapes, nothing else:

    workflow:<run_id>     the scheduled GitHub Actions run
    dispatch:<run_id>     a workflow_dispatch, by hand from the Actions page
    local:<user>@<host>   somebody ran ops/supervise.sh over SSH
    cron:<name>           a server cron entry, named after the entry

**`unknown` is a fault state, not a default.** On 03.10.2026 a supervisor run
appeared in the history at 17:29 that GitHub had no record of; the SSH log
placed it on a local session, and the field could not say so. A run without a
stated origin now shows up in the collected report as exactly that.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys

BASE = os.path.expanduser("~/moltstack")
HEARTBEAT = os.path.join(BASE, "data", "supervise_heartbeat.json")
HISTORY = os.path.join(BASE, "data", "supervision_history.jsonl")


def _write(path: str, text: str, append: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a" if append else "w") as f:
        f.write(text)
    os.chmod(path, 0o640)


ORIGIN_RE = re.compile(
    r"^(workflow:\d+|dispatch:\d+|local:[\w.-]+@[\w.-]+|cron:[\w.-]+)$")


def clean_origin(raw: str | None) -> str:
    """The origin, or `unknown` — never something in between.

    A malformed origin is worse than a missing one: it looks like provenance
    and is not. Anything that does not match the four shapes is recorded as
    unknown, with what was offered kept beside it.
    """
    if raw and ORIGIN_RE.match(raw):
        return raw
    return "unknown"


def stamp(origin: str = "unknown") -> int:
    """Stamped before the check, not after.

    A run that dies half way still proves the workflow reached the server, and
    that is the only thing this heartbeat claims. Stamping afterwards would
    make a crashed supervisor look like an absent one, and the server-side
    watchdog would then alarm about GitHub when the problem is here.
    """
    _write(HEARTBEAT, json.dumps({
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run": clean_origin(origin),
        "offered": origin if clean_origin(origin) == "unknown" and origin else None,
        "mode": os.environ.get("SUPERVISE_MODE", "check")}))
    return 0


def history(path: str, origin: str = "unknown") -> int:
    """One row per run, so Sunday can count instead of recollect."""
    try:
        doc = json.load(open(path))
    except Exception as e:
        row = {"light": "broken", "error": f"{type(e).__name__}: {e}",
               "green": 0, "yellow": 0, "red": 0, "offenders": {}}
    else:
        f = doc.get("findings") or []
        row = {"light": doc.get("light", "broken"),
               "green": sum(1 for x in f if x.get("light") == "green"),
               "yellow": sum(1 for x in f if x.get("light") == "yellow"),
               "red": sum(1 for x in f if x.get("light") == "red"),
               # Only the ones that were not green. A history that stores every
               # check every hour is a log; this has to stay countable.
               "offenders": {x["check"]: {"light": x["light"],
                                          "fix": x.get("fix") or None}
                             for x in f if x.get("light") != "green"}}
    row.update({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "run": clean_origin(origin),
                "offered": origin if clean_origin(origin) == "unknown" and origin
                else None,
                "mode": os.environ.get("SUPERVISE_MODE", "check")})
    _write(HISTORY, json.dumps(row, sort_keys=True) + "\n", append=True)
    return 0


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "stamp":
        return stamp(argv[1] if len(argv) > 1 else "unknown")
    if argv[0] == "history" and len(argv) > 1:
        return history(argv[1], argv[2] if len(argv) > 2 else "unknown")
    print(f"unknown: {' '.join(argv)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
