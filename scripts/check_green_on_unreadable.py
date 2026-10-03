#!/usr/bin/env python3
"""No watcher reports green on a measurement it could not read.

A watchdog has two jobs and they are easy to confuse: say whether the thing it
watches is healthy, and say whether it managed to look. When the second fails
and it answers the first anyway, it answers with the only value that is always
available — green.

Found on 2026-10-03. The invariant b-registry-equals-pypi read the MCP registry
through `servers[0]`, which is the *oldest* of five published versions, and
reported "PyPI 1.2.4 against registry 0.3.2 — DRIFT". There was no drift: the
registry's own `isLatest` flag sits on 1.2.4. The parser was unreadable and the
check answered anyway. The same morning, two of the catalogue's own invariants
had the same shape for the same reason.

So this is a check about checks. It walks the Python that reports health and
finds the two shapes that produce green out of nothing:

  1. a green verdict built inside an `except` handler — the call failed, and
     the answer is still ok
  2. a green verdict whose own detail text says it did not measure —
     "unreachable", "skipped", "could not", "no answer"

Shape 1 is the structural one and is what the invariant counts. A watchdog may
legitimately decide that an unreachable third-party index is not its business
to alarm on — but then it says so as a WARN with a reason, not as a pass.
"""
import ast
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Files that report health. A new one is added here, which is the point: the
# list is a declaration, and an unlisted reporter is invisible to this check.
WATCHERS = [
    "agents/watchdog.py",
    "agents/supervision.py",
    "agents/voice_gate.py",
    "scripts/selftest.py",
    "scripts/selfheal.py",
    "scripts/task_watch.py",
    "scripts/r2_settlement_watch.py",
    "scripts/threadwatch.py",
    "scripts/gate_measure.py",
    "scripts/gate_probe.py",
    "agents/comment_gate.py",
    "agents/proof_post.py",
]

# Words a verdict uses when it is really saying "I did not look".
BLIND = ("unreachable", "skipped", "could not", "not reachable", "no answer",
         "timeout", "timed out", "unavailable", "n/a", "nicht erreichbar",
         "ohne antwort", "übersprungen", "uebersprungen")

GREEN_KEYS = ("ok", "healthy", "pass", "green")


def green_dict(node):
    """The dict is a verdict that says green, or None."""
    if not isinstance(node, ast.Dict):
        return None
    pairs = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            pairs[k.value] = v
    # A verdict that declares itself unverifiable is not claiming health. The
    # watchdog already had that word for one check (the x402 validator, marked
    # ❔ rather than ✅); this makes it the general escape hatch, and the only
    # one. Declaring is allowed, passing silently is not.
    declared = pairs.get("unverifiable")
    if isinstance(declared, ast.Constant) and declared.value is True:
        return None
    for key in GREEN_KEYS:
        v = pairs.get(key)
        if isinstance(v, ast.Constant) and v.value is True:
            return pairs
    return None


def text_of(node):
    """Whatever string content a detail expression carries, concatenated."""
    out = []
    for n in ast.walk(node) if node is not None else []:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
    return " ".join(out).lower()


def scan(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [(0, "UNREADABLE", f"does not parse: {exc}")]

    # Mark every node that sits inside an except handler, and remember which
    # enclosing function each node belongs to.
    in_except, func_of = set(), {}

    def walk(node, handler, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if handler:
            in_except.add(node)
        func_of[node] = func
        for child in ast.iter_child_nodes(node):
            if isinstance(node, ast.Try) and child in node.handlers:
                walk(child, True, func)
            else:
                walk(child, handler or isinstance(node, ast.ExceptHandler), func)

    walk(tree, False, None)

    findings = []
    for node in ast.walk(tree):
        pairs = green_dict(node)
        if pairs is None:
            continue
        where = func_of.get(node) or "?"
        detail = text_of(pairs.get("detail") or pairs.get("surface"))
        if node in in_except:
            findings.append((node.lineno, "EXCEPT",
                             f"{where}(): green verdict built inside an except "
                             f"handler — the call failed and the answer is ok"))
        elif any(w in detail for w in BLIND):
            hit = next(w for w in BLIND if w in detail)
            findings.append((node.lineno, "BLIND",
                             f"{where}(): green verdict whose own detail says "
                             f"{hit!r} — it reports a pass for not having looked"))
    return findings


def main():
    only_structural = "--all" not in sys.argv
    total = 0
    for rel in WATCHERS:
        path = ROOT / rel
        if not path.exists():
            # A declared watcher that is gone is a finding, not a note. The
            # list is the declaration; a silent `continue` here would be the
            # same mistake one level up.
            total += 1
            print(f"{rel}: declared as a watcher and not in this checkout",
                  file=sys.stderr)
            continue
        for lineno, kind, msg in scan(path):
            if only_structural and kind == "BLIND":
                continue
            total += 1
            print(f"{rel}:{lineno}: [{kind}] {msg}", file=sys.stderr)
    print(total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
