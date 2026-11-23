#!/usr/bin/env python3
"""Routes that write for the owner of an API key ask whether that DID is revoked.

An API key stays valid after the DID behind it is revoked; `api_keys` knows
nothing about revocation. A route that resolves the key's owner and writes on
its behalf has to ask `agents.revoked_at` itself. /vc/aae/evaluate did not
until #1063 (17.11.2026); /vc/ipr/submit, /vc/ipr/{ipr_id}/outcome and /board
did not until ops REVOKE-ROUTES (23.11.2026). All four now call one function,
`did_revoked_refusal` in app/main.py, which answers 403 `did_revoked` before
anything is written.

This check reads app/main.py (AST, nothing is imported or executed) and counts
the guarded routes whose handler no longer calls that function. A route that
is not found at all counts too: a renamed route is a guard that went missing.

Prints one line per finding and the count last, which is what
scripts/selftest.py reads. Exit 0 when the count is 0, 1 otherwise.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

GUARD = "did_revoked_refusal"

# (method, path) of every route that must refuse a revoked key owner.
GUARDED = (
    ("post", "/vc/aae/evaluate"),
    ("post", "/vc/ipr/submit"),
    ("post", "/vc/ipr/{ipr_id}/outcome"),
    ("post", "/board"),
)

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "app" / "main.py"


def _routes(tree: ast.Module) -> dict[tuple[str, str], ast.AST]:
    found: dict[tuple[str, str], ast.AST] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                    and dec.args and isinstance(dec.args[0], ast.Constant)):
                found[(dec.func.attr, dec.args[0].value)] = node
    return found


def _calls(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
               for n in ast.walk(node))


def findings(source: str) -> list[str]:
    routes = _routes(ast.parse(source))
    out = []
    for method, path in GUARDED:
        handler = routes.get((method, path))
        if handler is None:
            out.append(f"FEHLT {method.upper()} {path}: route not found in app/main.py")
        elif not _calls(handler, GUARD):
            out.append(f"OHNE PRUEFUNG {method.upper()} {path} ({handler.name}, "
                       f"line {handler.lineno}): does not call {GUARD}")
    return out


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_SOURCE
    found = findings(path.read_text(encoding="utf-8"))
    for line in found:
        print(f"  {line}")
    if not found:
        print(f"  all {len(GUARDED)} routes call {GUARD}")
    print(len(found))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
