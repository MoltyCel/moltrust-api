#!/usr/bin/env python3
"""Reserved-names guard.

Fails when a reserved identifier appears in a file name, in file content, or in a commit
message. Occurrences that existed when the guard was introduced are listed in
.github/reserved-names-baseline as SHA-256 hashes, so the baseline does not repeat them.

Modes:
  ci                 all tracked files at HEAD, plus commit messages in $GUARD_RANGE
                     (e.g. "base..head") if set
  staged             files staged for commit (pre-commit hook)
  msg <file>         one commit message file (commit-msg hook)
  baseline           print baseline lines for every current occurrence

Standard library only.
"""
# Laeuft auch als Pre-Commit-Hook, dort mit der System-Python des Rechners.
# Auf 3.9 ist `list | None` ein TypeError beim Import, nicht erst im Aufruf.
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys

# Assembled from parts so this file does not itself contain the identifiers.
_A, _D, _N = "a" + "ae", "dr" + "aft", "0" + "4"
CONTENT = re.compile(r"(?:%s|%s)-%s" % (_A, _D, _N), re.IGNORECASE)
FILENAME = re.compile(r"(?:^|/)%s-%s[^/]*$" % (_A, _N), re.IGNORECASE)

# Short form: a bare revision number above the highest published one. It counts only next
# to a revision word on the same line, or anywhere in a file under SPEC_DIR. Dates
# (2026-04-05) and version strings (1.0-04) never match: the number must not follow a
# letter or digit, and must not be followed by a digit or another hyphen.
PUBLISHED_MAX = 2
SHORT = re.compile(r"(?<![0-9A-Za-z])-0([0-9])(?![0-9-])")
CONTEXT = re.compile(r"candidates?|revision|draft|step|§", re.IGNORECASE)
SPEC_DIR = "docs/spec-fakten/"


def short_form(line: str, path: str = "") -> bool:
    if not any(int(m.group(1)) > PUBLISHED_MAX for m in SHORT.finditer(line)):
        return False
    return path.startswith(SPEC_DIR) or bool(CONTEXT.search(line))


def reserved(line: str, path: str = "") -> bool:
    return bool(CONTENT.search(line)) or short_form(line, path)

BASELINE = ".github/reserved-names-baseline"
MAX_BYTES = 2_000_000


def _h(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True).stdout


def _exists(ref: str) -> bool:
    """Is `ref` a commit this clone actually has?

    `git rev-parse` echoes any well-formed 40-hex string back, present or not,
    so it answers nothing. `--verify ref^{commit}` is the question.
    """
    try:
        _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return True
    except subprocess.CalledProcessError:
        return False


def _commits_in(rng):
    """Commits whose messages are to be scanned. None = could not be answered.

    A force-push leaves `github.event.before` unreachable — actions/checkout
    does not fetch a discarded commit — so `git rev-list before..after` exits
    128 and this guard crashed with a traceback instead of scanning anything.
    It crashed on a routine amend, and a required check that goes red from a
    tooling error is the situation that invites an admin bypass.
    moltguard#58 hit it on 2026-10-06.

    The fallback keeps the coverage the pull_request event has: everything on
    the head that is not on the default branch. When that range is empty —
    a push to the default branch itself, where the head *is* the tip — it
    scans the head commit, because an empty range would otherwise skip the
    scan and report green. That hole was in the first version of this fix and
    a test caught it.
    """
    try:
        return _git("rev-list", rng).split()
    except subprocess.CalledProcessError:
        pass
    head = rng.rpartition("..")[2] or "HEAD"
    if not _exists(head):
        return None
    for base in ("origin/main", "main"):
        if not _exists(base):
            continue
        try:
            out = _git("rev-list", f"{base}..{head}").split()
        except subprocess.CalledProcessError:
            continue
        where = f"{base}..{head}" if out else head
        print(f"GUARD_RANGE {rng} does not resolve (force-push?); scanning "
              f"{where} instead", file=sys.stderr)
        return out or [head]
    print(f"GUARD_RANGE {rng} does not resolve and no default branch is "
          f"present; scanning only {head}", file=sys.stderr)
    return [head]


def _baseline() -> set:
    try:
        with open(BASELINE, encoding="utf-8") as fh:
            return {ln.split()[0] for ln in fh if ln.strip() and not ln.startswith("#")}
    except FileNotFoundError:
        return set()


def _scan_file(path: str, data: bytes):
    """Yield (hash, location) for every occurrence in one file."""
    if FILENAME.search(path):
        yield _h("name", path), f"{path} (file name)"
    if len(data) > MAX_BYTES or b"\0" in data[:8192]:
        return
    text = data.decode("utf-8", errors="replace")
    for no, line in enumerate(text.splitlines(), 1):
        if reserved(line, path):
            yield _h("line", path, line.strip()), f"{path}:{no}"


def _tracked():
    for path in _git("ls-files", "-z").split("\0"):
        if path and os.path.isfile(path):
            with open(path, "rb") as fh:
                yield path, fh.read()


def _staged():
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    for path in out.split("\0"):
        if path:
            blob = subprocess.run(["git", "show", f":{path}"], capture_output=True).stdout
            yield path, blob


def _report(hits) -> int:
    allowed = _baseline()
    new = [loc for h, loc in hits if h not in allowed]
    for loc in new:
        print(f"reserved identifier: {loc}", file=sys.stderr)
    if new:
        print(f"{len(new)} occurrence(s) of a reserved identifier. Remove them; the "
              f"baseline only covers what existed when the guard was added.", file=sys.stderr)
        return 1
    return 0


def main(argv) -> int:
    mode = argv[1] if len(argv) > 1 else "ci"
    if mode == "baseline":
        # Hashes only: a path or a line could itself carry the identifier.
        for h in sorted({h for path, data in _tracked() for h, _loc in _scan_file(path, data)}):
            print(h)
        return 0
    if mode == "msg":
        with open(argv[2], encoding="utf-8", errors="replace") as fh:
            body = "".join(ln for ln in fh if not ln.startswith("#"))
        if any(reserved(ln) for ln in body.splitlines()):
            print("reserved identifier in the commit message", file=sys.stderr)
            return 1
        return 0
    if mode == "staged":
        hits = [x for path, data in _staged() for x in _scan_file(path, data)]
        return _report(hits)
    hits = [x for path, data in _tracked() for x in _scan_file(path, data)]
    rc = _report(hits)
    rng = os.environ.get("GUARD_RANGE", "")
    if rng and not rng.startswith("0000000"):
        shas = _commits_in(rng)
        if shas is None:
            # No green on a scan that did not run.
            print(f"UNREADABLE: neither {rng} nor its head resolves — no commit "
                  "message was scanned", file=sys.stderr)
            return 1
        for sha in shas:
            try:
                msg = _git("log", "-1", "--format=%B", sha)
            except subprocess.CalledProcessError:
                # Ein Commit, der zwischen rev-list und hier verschwindet, ist
                # kein grüner Zustand.
                print(f"UNREADABLE: commit {sha[:12]} not readable — no message "
                      "scanned", file=sys.stderr)
                return 1
            if any(reserved(ln) for ln in msg.splitlines()):
                print(f"reserved identifier in the message of commit {sha[:12]}",
                      file=sys.stderr)
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
