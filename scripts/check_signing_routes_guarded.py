#!/usr/bin/env python3
"""Every route that signs for a DID or sends from the productive key checks
the caller first.

Until 21.10.2026 four routes did not: POST /credentials/issue and
POST /music/credential/issue issued for any DID the caller named, and
POST /anchors/batch and POST /identity/erc8004/validate sent from the
productive signer for an API key with no agent behind it. Since then each
handler calls a guard from app/sign_guard.py before it signs, writes, archives
or schedules anything.

This check reads app/main.py with the AST, finds each handler by its route
and confirms the guard call is there and comes before the first call that has
a side effect. A refactor that drops the guard, or moves a signing call above
it, turns this red.

Last line is the number of routes that fail. Exit 0 with none, 1 with some,
2 when app/main.py cannot be read.
"""
from __future__ import annotations

import ast
import os
import sys

# route -> guard the handler has to call
GUARDED = {
    "/credentials/issue": "_require_did_owner_or_admin",
    "/music/credential/issue": "_require_did_owner_or_admin",
    "/anchors/batch": "require_bound_key",
    "/identity/erc8004/validate": "require_bound_key",
}

# calls that sign, write, archive, charge or send
SIDE_EFFECTS = {
    "issue_and_store_vc", "issue_credential", "sign_credential", "archive",
    "execute", "executemany", "anchor_calldata_for_agents", "anchor_to_base",
    "schedule_music_anchor", "reputation_feedback_for_agents", "add_task",
}


def _name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _post_paths(fn: ast.AST) -> list[str]:
    out = []
    for d in getattr(fn, "decorator_list", []):
        if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr == "post" and d.args
                and isinstance(d.args[0], ast.Constant)):
            out.append(d.args[0].value)
    return out


def check(source: str) -> list[str]:
    """Problems found in ``source``, one line each; empty when all routes hold."""
    tree = ast.parse(source)
    handlers = {}
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for path in _post_paths(node):
                handlers[path] = node
    problems = []
    for path, guard in GUARDED.items():
        fn = handlers.get(path)
        if fn is None:
            problems.append(f"{path}: handler not found")
            continue
        calls = sorted(
            ((c.lineno, c.col_offset, _name(c)) for c in ast.walk(fn)
             if isinstance(c, ast.Call)),
            key=lambda x: (x[0], x[1]))
        guard_at = next((ln for ln, _, n in calls if n == guard), None)
        if guard_at is None:
            problems.append(f"{path} ({fn.name}): no call to {guard}")
            continue
        first = next(((ln, n) for ln, _, n in calls if n in SIDE_EFFECTS), None)
        if first and first[0] < guard_at:
            problems.append(f"{path} ({fn.name}): {first[1]} at line {first[0]} "
                            f"runs before {guard} at line {guard_at}")
    return problems


def main() -> int:
    root = os.environ.get("MOLTRUST_ROOT",
                          os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(os.path.join(root, "app", "main.py"), encoding="utf-8") as fh:
            source = fh.read()
        problems = check(source)
    except (OSError, SyntaxError) as exc:
        print(f"UNREADABLE: {type(exc).__name__}: {exc}")
        return 2
    for p in problems:
        print(f"  UNGUARDED {p}")
    if not problems:
        print(f"  all {len(GUARDED)} routes guarded")
    print(len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
