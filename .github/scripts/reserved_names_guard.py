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
import hashlib
import os
import re
import subprocess
import sys

# Assembled from parts so this file does not itself contain the identifiers.
_A, _D, _N = "a" + "ae", "dr" + "aft", "0" + "4"
CONTENT = re.compile(r"(?:%s|%s)-%s" % (_A, _D, _N), re.IGNORECASE)
FILENAME = re.compile(r"(?:^|/)%s-%s[^/]*$" % (_A, _N), re.IGNORECASE)

BASELINE = ".github/reserved-names-baseline"
MAX_BYTES = 2_000_000


def _h(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True).stdout


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
        if CONTENT.search(line):
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
        if CONTENT.search(body):
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
        for sha in _git("rev-list", rng).split():
            if CONTENT.search(_git("log", "-1", "--format=%B", sha)):
                print(f"reserved identifier in the message of commit {sha[:12]}",
                      file=sys.stderr)
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
