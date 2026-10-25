#!/usr/bin/env python3
"""pip-audit findings split into production dependencies and tooling.

Run with the production venv's interpreter, so the dependency closure is read
from what is installed there:

    venv/bin/pip-audit -f json | venv/bin/python -I scripts/pip_audit_split.py requirements.txt

Prints one line per finding group, each starting with ALERT, INFO or OK, for
security_check.sh to pass on.

Why (ops SEC-1025-API, 25.10.2026): the report counted every package in the
venv. pip itself and filelock (which arrives with pip-audit, installed into the
venv by security_check.sh) carried 5 of the 118 advisories, and nothing in
requirements.txt reaches them. Only a production dependency with a fixed
release is something to act on; the rest is listed, not alerted.
"""
from __future__ import annotations

import json
import re
import sys
from importlib import metadata as md

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def top_level(lines) -> list[str]:
    names = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(canon(Requirement(line).name))
    return names


def _installed_requires(name: str) -> list[str]:
    try:
        return md.requires(name) or []
    except md.PackageNotFoundError:
        return []


def closure(roots, requires=_installed_requires) -> set[str]:
    """Every package reached from roots through non-extra requirements."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for raw in requires(name):
            req = Requirement(raw)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            stack.append(canon(req.name))
    return seen


def _min_fix(vuln: dict, installed: str) -> Version | None:
    fixes = []
    for f in vuln.get("fix_versions") or []:
        try:
            v = Version(f)
            if v > Version(installed):
                fixes.append(v)
        except InvalidVersion:
            continue
    return min(fixes) if fixes else None


def classify(deps: list[dict], prod: set[str]) -> list[str]:
    out: list[str] = []
    prod_fix, prod_nofix, tooling = [], [], []
    for dep in deps:
        vulns = dep.get("vulns") or []
        if not vulns:
            continue
        name, version = dep["name"], dep["version"]
        fixable = [_min_fix(v, version) for v in vulns]
        need = max((f for f in fixable if f), default=None)
        nofix = [v["id"] for v, f in zip(vulns, fixable) if f is None]
        if canon(name) not in prod:
            tooling.append(f"{name} {version} ({len(vulns)})")
            continue
        if need:
            prod_fix.append(f"{name} {version} -> {need} ({len(vulns) - len(nofix)})")
        if nofix:
            prod_nofix.append(f"{name} {version} ({', '.join(nofix)})")
    if prod_fix:
        out.append(f"ALERT pip-audit: production dependencies with a fixed release: {'; '.join(prod_fix)}")
    if prod_nofix:
        out.append(f"INFO pip-audit: production advisories without a fixed release: {'; '.join(prod_nofix)}")
    if tooling:
        out.append(f"INFO pip-audit: tooling in the venv, not reached from requirements.txt: {'; '.join(tooling)}")
    if not prod_fix:
        out.append("OK pip-audit: no fixable advisory in production dependencies")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: pip_audit_split.py requirements.txt < pip-audit.json", file=sys.stderr)
        return 2
    try:
        data = json.load(sys.stdin)
        with open(argv[1], encoding="utf-8") as fh:
            roots = top_level(fh)
    except (OSError, ValueError) as exc:
        print(f"ALERT pip-audit output could not be read: {type(exc).__name__}: {exc}")
        return 2
    deps = data["dependencies"] if isinstance(data, dict) else data
    for line in classify(deps, closure(roots)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
