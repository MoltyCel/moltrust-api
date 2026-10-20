#!/usr/bin/env python3
"""Every key in ~/.moltrust_secrets appears exactly once.

The file is sourced with `set -a; . ~/.moltrust_secrets`, so a repeated key is
not an error anywhere — the last assignment wins silently. On 2026-10-04
HEALTHCHECK_URL stood three times: twice as the literal placeholder
hc-ping.com/DEINE-UUID and once with the real UUID. The real one happened to be
last, so the watchdog pinged the right receiver. Sorting the file, or appending
a key someone believed was missing, would have armed it.

Names and line numbers only. A secrets checker that prints values to prove a
point has defeated itself, so values never leave this process — not in the
output, not in an exception, not in a log line.
"""
import argparse
import collections
import os
import re
import sys

PATH = os.path.expanduser(os.environ.get("MOLTRUST_SECRETS",
                                         "~/.moltrust_secrets"))
# KEY=…, optionally exported, optionally indented. Anything else is a comment,
# a blank line, or shell the file happens to contain.
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

# A floor, because "no duplicates" is also what a truncated or empty file says.
# The file held about a hundred keys on 2026-10-05; twenty is far below that and
# far above anything a healthy file would drop to.
MIN_KEYS = 20


def duplicates(path: str):
    """{key: [line numbers]} for every key assigned more than once."""
    seen = collections.defaultdict(list)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if line.lstrip().startswith("#"):
                continue
            m = ASSIGN.match(line)
            if m:
                seen[m.group(1)].append(n)
    return {k: v for k, v in seen.items() if len(v) > 1}, len(seen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=PATH)
    a = ap.parse_args()

    if not os.path.exists(a.path):
        # Not a clean result. The file is the thing being checked.
        print(f"UNREADABLE: {a.path} gibt es nicht", file=sys.stderr)
        print(-1)
        return 2
    try:
        dupes, total = duplicates(a.path)
    except OSError as exc:
        print(f"UNREADABLE: {type(exc).__name__}", file=sys.stderr)
        print(-1)
        return 2

    if total < MIN_KEYS:
        # Zero duplicates is what an empty file reports too, and that file is
        # the one holding every credential on this machine.
        print(f"UNREADABLE: nur {total} Schluessel gelesen, Boden {MIN_KEYS} — "
              f"die Datei ist abgeschnitten oder leer", file=sys.stderr)
        print(-1)
        return 2

    for key in sorted(dupes):
        lines = ", ".join(str(n) for n in dupes[key])
        print(f"MEHRFACH: {key} in Zeile {lines} — gesourct gewinnt die letzte",
              file=sys.stderr)
    print(f"{total} Schluessel geprueft", file=sys.stderr)
    print(len(dupes))
    return 1 if dupes else 0


if __name__ == "__main__":
    sys.exit(main())
