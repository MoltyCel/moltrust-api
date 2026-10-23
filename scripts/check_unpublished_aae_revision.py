#!/usr/bin/env python3
"""No file in this public repository names an unpublished AAE revision.

The AAE draft is published on the IETF datatracker as
draft-kroehl-agentic-trust-aae-00, -01 and -02. Every citation in this
repository goes to a published revision. A local working revision with a
higher number is not public, and naming it here says something about work that
has not been published (ops PUB-04, 2026-10-23: five places on main had carried
such a name since 2026-06-01).

The check reads every tracked text file and reports each line that names the
fourth revision of the AAE draft. The patterns are written so that this file and
its test do not name it themselves: the digit is joined in at runtime.

Third-party drafts with the same revision number are not hits. Their names carry
the draft's own name before the number ("draft-agentic-ai-usecases-requirements-"
followed by the number, "UCR-" followed by the number), and no pattern below
matches that.

Exit 0 = no hit, 1 = hits (one "path:line: text" each), 2 = git not usable.
"""
from __future__ import annotations

import re
import subprocess
import sys

_N = "0" + "4"

PATTERNS = (
    # "AAE draft-NN", "AAE -NN", "aae-NN", "aae_NN"
    re.compile(r"\baae[\s_-]*(?:draft[\s_-]*)?-?" + _N + r"\b", re.IGNORECASE),
    # "draft-NN" standing alone (a named third-party draft has its name in between)
    re.compile(r"\bdraft-" + _N + r"\b", re.IGNORECASE),
    # the datatracker name of the AAE draft
    re.compile(r"agentic-trust-aae-" + _N + r"\b", re.IGNORECASE),
    # "Arbeitsrevision -NN" and a quoted bare "-NN"
    re.compile(r"arbeitsrevision\s*[„\"'«]?-" + _N + r"\b", re.IGNORECASE),
    re.compile(r"[„\"'«]-" + _N + r"[“\"'»]"),
)


def hits_in(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every line of text that names the revision."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if any(p.search(line) for p in PATTERNS):
            out.append((n, line.strip()))
    return out


def tracked_files(repo: str = ".") -> list[str]:
    r = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True)
    return [p for p in r.stdout.decode().split("\0") if p]


def scan(repo: str = ".") -> list[str]:
    found = []
    for path in tracked_files(repo):
        try:
            with open(f"{repo}/{path}", "rb") as f:
                raw = f.read()
        except OSError:
            continue  # a tracked path missing from the work tree has no text to check
        if b"\0" in raw[:8192]:
            continue  # binary
        for n, line in hits_in(raw.decode("utf-8", errors="replace")):
            found.append(f"{path}:{n}: {line[:200]}")
    return found


def main() -> int:
    try:
        found = scan()
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"UNREADABLE git ls-files failed: {e}")
        return 2
    for line in found:
        print(line)
    if found:
        print(f"{len(found)} line(s) name an unpublished AAE revision; cite a published one (-00, -01, -02).")
        return 1
    print("no unpublished AAE revision named")
    return 0


if __name__ == "__main__":
    sys.exit(main())
