"""The supervisor's own bookkeeping: heartbeat and one line per run.

Separate from scripts/selftest.py and agents/supervision.py because those are
read-only and must stay that way — a diagnostic that writes is a diagnostic
that can change what it measures. The supervisor is allowed to write; it is
recording that it ran, not deciding anything.

    ops/supervise_record.py stamp                 # heartbeat, before the check
    ops/supervise_record.py history <json-file>   # one history row, after it
"""
import datetime
import json
import os
import sys

BASE = os.path.expanduser("~/moltstack")
HEARTBEAT = os.path.join(BASE, "data", "supervise_heartbeat.json")
HISTORY = os.path.join(BASE, "data", "supervision_history.jsonl")


def _write(path: str, text: str, append: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a" if append else "w") as f:
        f.write(text)
    os.chmod(path, 0o640)


def stamp() -> int:
    """Stamped before the check, not after.

    A run that dies half way still proves the workflow reached the server, and
    that is the only thing this heartbeat claims. Stamping afterwards would
    make a crashed supervisor look like an absent one, and the server-side
    watchdog would then alarm about GitHub when the problem is here.
    """
    _write(HEARTBEAT, json.dumps({
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        # "unknown", not "manual": the deploy key carries a forced command, so
        # no environment crosses the SSH boundary and the server genuinely
        # cannot tell a workflow run from a hand-run one. Saying "manual" would
        # assert something nobody checked.
        "run": os.environ.get("GITHUB_RUN_ID", "unknown"),
        "mode": os.environ.get("SUPERVISE_MODE", "check")}))
    return 0


def history(path: str) -> int:
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
                "run": os.environ.get("GITHUB_RUN_ID", "unknown"),
                "mode": os.environ.get("SUPERVISE_MODE", "check")})
    _write(HISTORY, json.dumps(row, sort_keys=True) + "\n", append=True)
    return 0


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "stamp":
        return stamp()
    if argv[0] == "history" and len(argv) > 1:
        return history(argv[1])
    print(f"unknown: {' '.join(argv)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
