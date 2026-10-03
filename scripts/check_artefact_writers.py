#!/usr/bin/env python3
"""Exactly one declared writer per public artefact, counted by code.

The rule comes from 2026-09-27: registry-proof.json lay in the web root for
seven and a half hours listing 50 partner DIDs, because two paths produced it
and only one had been changed. Two paths to one public artefact is the defect,
not the cause of a defect.

The first version of the check was `grep -rl registry-proof.json | xargs grep -l
'open(.*w\\|install -m 644'`, with a cap of two. On 2026-10-03 it reported four.
Two of the four were prose: a comment in app/main.py recalling the incident, and
a docstring in scripts/check_cron_sudo.py quoting the very install command this
file exists to talk about. A check that matches text finds writing where
somebody only wrote *about* writing — which is how a watcher ends up counting
its own documentation.

So: comments and docstrings come out first, then the file has to both name the
artefact and carry a write construct, and the writers are a positive list. A
new writer is a finding and has to be declared on purpose, which is the whole
point of the rule.
"""
import ast
import io
import pathlib
import re
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEARCH = ("scripts", "app", "agents", "ops")

# Positive list, per artefact. CLAUDE.md: an exclusion is a positive list —
# a missing line here produces a finding, never silence.
WRITERS = {
    "registry-proof.json": [
        "scripts/registry_proof_export.py",   # builds the document
        "scripts/registry_proof_publish.sh",  # checks it, then installs it
    ],
}

WRITE_PY = re.compile(r"""open\s*\([^)]*["'][wax]b?\+?["']|
                          \.write_text\s*\(|\.write_bytes\s*\(|
                          shutil\.(?:copy|copyfile|copy2|move)\s*\(|
                          json\.dump\s*\(|os\.replace\s*\(""", re.X)
WRITE_SH = re.compile(r">\s*\S|\binstall\b|\btee\b|\bcp\b|\bmv\b|\bwebinstall\b")


def strip_python(src):
    """The code with comments and docstrings gone, other string literals kept.

    The distinction matters and the first attempt at this file got it wrong:
    blanking every string literal removes the path a real writer must name. A
    writer says open("…/registry-proof.json", "w"); prose says it in a comment
    or a docstring. So comments go by token, docstrings and bare
    string-statements go by position, and everything else stays.

    Blanked rather than deleted, so a line number still means what it says.
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        tree = ast.parse(src)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None

    prose = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                prose.add((first.lineno, first.col_offset))
        if isinstance(body, list):
            for stmt in body:
                # A string used as a statement is a comment with quotes.
                if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)):
                    prose.add((stmt.lineno, stmt.col_offset))

    out = []
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            out.append("")
        elif tok.type == tokenize.STRING and tok.start in prose:
            out.append("\n" * tok.string.count("\n"))
        else:
            out.append(tok.string)
    return "".join(out)


def strip_shell(src):
    return "\n".join(re.sub(r"(?<!\\)#.*$", "", line) for line in src.splitlines())


def names_artefact_in_code(path, artefact):
    """The file refers to the artefact somewhere that is not prose."""
    src = path.read_text(encoding="utf-8", errors="replace")
    if artefact not in src:
        return False
    if path.suffix == ".py":
        code = strip_python(src)
        if code is None:
            return None  # unreadable, and that is a finding of its own
    else:
        code = strip_shell(src)
    return artefact in code


def writes_anything(path):
    src = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        code = strip_python(src) or ""
        return bool(WRITE_PY.search(code))
    return bool(WRITE_SH.search(strip_shell(src)))


def main():
    findings = 0
    for artefact, declared in WRITERS.items():
        declared_paths = []
        for rel in declared:
            p = ROOT / rel
            if not p.exists():
                print(f"DECLARED WRITER MISSING: {rel} for {artefact}",
                      file=sys.stderr)
                findings += 1
                continue
            declared_paths.append(rel)
            if not writes_anything(p):
                print(f"DECLARED WRITER DOES NOT WRITE: {rel} for {artefact}",
                      file=sys.stderr)
                findings += 1

        for folder in SEARCH:
            base = ROOT / folder
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if p.suffix not in (".py", ".sh") or not p.is_file():
                    continue
                rel = str(p.relative_to(ROOT))
                if rel in declared_paths:
                    continue
                named = names_artefact_in_code(p, artefact)
                if named is None:
                    print(f"UNREADABLE {rel}: does not tokenise", file=sys.stderr)
                    findings += 1
                    continue
                if named and writes_anything(p):
                    print(f"UNDECLARED WRITER: {rel} names {artefact} in code "
                          f"and writes files", file=sys.stderr)
                    findings += 1
    print(findings)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
