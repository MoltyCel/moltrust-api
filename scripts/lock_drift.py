#!/usr/bin/env python3
"""What the server has installed, against what requirements.lock pins.

The lock has been in the repository since 2026-09-04 and nothing ever
installed from it: the deploy installs `-r requirements.txt`, where most
entries are ranges, so pip keeps whatever already satisfies the range. On
2026-10-17 that was 67 of 106 pinned packages away from the lock, a2a-sdk
among them (lock 1.1.2, server 1.1.0) -- the finding that started ops DEP-1.

Wiring the lock straight into the install was turned down on 2026-09-04 for a
reason that still holds (docs/BACKLOG.md): it would raise every divergence at
once, and some of them are major versions with known breaking changes (mcp
1.28 -> 2.1 renamed FastMCP, web3 7 -> 8, starlette 1.3 -> 1.6). So the lock
keeps documenting, and this script is the fence instead: every divergence that
exists today is written down once, with the version pair it had when it was
recorded, and the deploy refuses anything that is not on that list.

A recorded line is drift to be closed, not drift that is allowed. The list is
meant to get shorter; a line whose version pair no longer matches reality is
reported as stale and does not fail the run, because a deploy that closed a
divergence must not be the one that gets refused for it.

Exit codes: 0 nothing unrecorded, 1 unrecorded divergence, 2 cannot read an
input.
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib.metadata import distributions
from pathlib import Path

MISSING = "fehlt"

PIN = re.compile(r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?==([^\s\\;]+)")


def normalize(name: str) -> str:
    """PEP 503 name: pip treats a2a_sdk, A2A-SDK and a2a.sdk as one package."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def read_lock(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = PIN.match(line.strip())
        if match:
            pins[normalize(match.group(1))] = match.group(3)
    return pins


def read_known(path: Path) -> dict[str, tuple[str, str]]:
    """name -> (lock version, installed version) as recorded, `fehlt` for absent."""
    known: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return known
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(f"not three fields: {raw!r}")
        known[normalize(parts[0])] = (parts[1], parts[2])
    return known


def installed_versions() -> dict[str, str]:
    found: dict[str, str] = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if name:
            found[normalize(name)] = dist.version
    return found


def compare(
    pins: dict[str, str], installed: dict[str, str], known: dict[str, tuple[str, str]]
) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    """(unrecorded, recorded and still true, recorded but stale), each sorted."""
    # Normalized again here, not only where the versions are read: a caller
    # that hands in a map of its own must not be able to produce a green run
    # by spelling a2a_sdk with an underscore.
    installed = {normalize(name): version for name, version in installed.items()}
    unrecorded: list[tuple[str, str, str]] = []
    holds: list[str] = []
    for name, want in sorted(pins.items()):
        have = installed.get(name, MISSING)
        if have == want:
            continue
        if known.get(name) == (want, have):
            holds.append(name)
        else:
            unrecorded.append((name, want, have))
    stale = sorted(set(known) - set(holds))
    return unrecorded, holds, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default="requirements.lock", type=Path)
    parser.add_argument("--known", default="ops/lockdrift-bekannt.txt", type=Path)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print the unrecorded divergences and the counts, nothing else",
    )
    args = parser.parse_args(argv)

    try:
        pins = read_lock(args.lock)
        known = read_known(args.known)
    except (OSError, ValueError) as err:
        print(f"lock drift: cannot read an input: {err}", file=sys.stderr)
        return 2
    if not pins:
        print(f"lock drift: no pins in {args.lock}", file=sys.stderr)
        return 2

    installed = installed_versions()
    unrecorded, holds, stale = compare(pins, installed, known)

    for name, want, have in unrecorded:
        print(f"lock drift: {name} lock {want}, installed {have} -- not recorded")
    if not args.quiet:
        for name in stale:
            want, have = known[name]
            print(
                f"lock drift: {name} was recorded as lock {want} / installed {have}, "
                f"now {installed.get(name, MISSING)} against lock "
                f"{pins.get(name, MISSING)} -- the line can go"
            )
    print(
        f"lock drift: {len(pins)} pinned, {len(holds)} recorded, "
        f"{len(unrecorded)} unrecorded, {len(stale)} stale"
    )
    return 1 if unrecorded else 0


if __name__ == "__main__":
    sys.exit(main())
