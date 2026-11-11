#!/usr/bin/env python3
"""Effects at module level: code that acts when a file is merely loaded.

On 2026-10-09 scripts/telegram_hn_remind.py sent the same Telegram message
three times because its send stood at module level: a probe that only loaded
the file (runpy, run_name != "__main__") sent each time. WORKFLOW 18: a module
body defines, it does not act; every outward effect sits in a function behind
`if __name__ == "__main__"`.

This walks every statement that runs on import — the module body and the
bodies of module-level if/try/with/for/while, but not functions, classes or an
`if __name__ == "__main__"` block — and classifies each call found there.

    python3 scripts/module_effects.py [ROOT ...]          list findings
    python3 scripts/module_effects.py --check ROOT        exit 1 on findings not
                                                           in the baseline
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

# Classification by the last name part and its prefix. Anything not matched
# is "other": listed in the appendix, not counted as an effect.
SEND = {"post", "send_telegram", "send_telegram_message", "sendMessage", "send_message",
        "send_befunde", "urlopen", "create_tweet", "post_tweet", "sendmail", "send_post",
        "send_once", "send_email", "publish"}
NET_PREFIX = ("requests", "httpx", "session", "client", "s", "http", "req_lib")
WALLET = {"from_key", "sign_transaction", "send_raw_transaction", "build_transaction",
          "transact", "sign", "dual_sign", "sign_payload", "sign_eddsa_jcs_2022", "sign_message"}
DELETE = {"remove", "unlink", "rmtree", "rmdir"}
DB = {"execute", "executemany", "commit", "copy_records_to_table"}
DB_CONNECT_PREFIX = ("psycopg2", "asyncpg", "sqlite3", "psycopg")
PROCESS = {"run", "Popen", "call", "check_call", "check_output", "system", "run_until_complete",
           "execv", "execvp", "spawn", "sleep", "exit"}
PROCESS_PREFIX = ("subprocess", "os", "asyncio", "uvicorn", "loop", "time", "sys")
def dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func) + "()"
    if isinstance(node, ast.Subscript):
        return dotted(node.value) + "[]"
    return type(node).__name__


def _is_main_guard(test) -> bool:
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and test.left.id == "__name__" and any(
                isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators))


def module_level_statements(body):
    for st in body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                           ast.Import, ast.ImportFrom)):
            continue
        if isinstance(st, ast.If):
            if _is_main_guard(st.test):
                continue
            yield st.test
            yield from module_level_statements(st.body)
            yield from module_level_statements(st.orelse)
            continue
        if isinstance(st, ast.Try):
            for part in (st.body, st.orelse, st.finalbody):
                yield from module_level_statements(part)
            for h in st.handlers:
                yield from module_level_statements(h.body)
            continue
        if isinstance(st, (ast.With, ast.AsyncWith)):
            for it in st.items:
                yield it.context_expr
            yield from module_level_statements(st.body)
            continue
        if isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
            yield st.iter if isinstance(st, (ast.For, ast.AsyncFor)) else st.test
            yield from module_level_statements(st.body)
            continue
        yield st


def calls_in(node):
    """Calls in node, not descending into lambdas, comprehensions' bodies stay in."""
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(n, ast.Call):
            yield n
        stack.extend(ast.iter_child_nodes(n))


def classify(name: str, call: ast.Call) -> str:
    parts = name.replace("()", "").split(".")
    last, prefix = parts[-1], parts[0]
    if last == "open" and prefix in ("open", "io", "builtins", "codecs") or name == "open":
        mode = ""
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            mode = str(call.args[1].value)
        for kw in call.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        return "file write" if any(c in mode for c in "wax+") else "other"
    if last in SEND or (last in ("request", "put", "patch", "delete") and prefix in NET_PREFIX):
        return "send"
    if last == "get" and prefix in ("requests", "httpx", "req_lib"):
        return "network read"
    if last in WALLET or prefix in ("x_meter", "wallet", "w3", "web3") and last not in ("keccak", "to_checksum_address", "is_address", "hex"):
        return "wallet/sign/x_meter"
    if last in DELETE and (prefix in ("os", "shutil") or len(parts) > 1):
        return "delete"
    if last in DB or (last == "connect" and prefix in DB_CONNECT_PREFIX):
        return "db write"
    if last in ("write", "write_text", "write_bytes", "touch", "chmod", "symlink_to") \
            or (last in ("dump",) and prefix in ("json", "yaml", "pickle")) \
            or (prefix == "shutil") or (last in ("rename", "replace") and prefix == "os"):
        return "file write"
    if last in ("mkdir", "makedirs"):
        return "dir create"
    if name in ("main", "main()") or last in ("main",) and len(parts) == 1:
        return "process (runs main)"
    if last in PROCESS and prefix in PROCESS_PREFIX:
        return "process/exit/sleep"
    if name == "logging.basicConfig" or last.endswith("FileHandler"):
        return "logging config"
    return "other"


def scan_file(path: str, rel: str) -> list[dict]:
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read(), filename=rel)
    except SyntaxError as e:
        return [{"file": rel, "line": e.lineno or 0, "kind": "unparseable", "call": str(e.msg)}]
    out = []
    for st in module_level_statements(tree.body):
        for c in calls_in(st):
            name = dotted(c.func)
            out.append({"file": rel, "line": c.lineno, "kind": classify(name, c), "call": name})
    return out


SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "tests", "test",
             "site-packages", ".claude", "fixtures", "vectors"}


def _git_files(root: str) -> list[str] | None:
    """Tracked files plus untracked ones that are not ignored, or None outside git.

    A gitignored file (local review kits, caches) is not deployed code; an
    untracked, not ignored one is exactly what reaches a checkout past CI.
    """
    import subprocess
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others",
                            "--exclude-standard"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return sorted(set(l for l in r.stdout.splitlines() if l.endswith(".py")))


def scan(root: str) -> list[dict]:
    found = []
    listed = _git_files(root)
    if listed is not None:
        for rel in listed:
            parts = rel.split("/")
            if any(p in SKIP_DIRS or p.startswith(".") for p in parts[:-1]):
                continue
            full = os.path.join(root, rel)
            if os.path.isfile(full):
                found.extend(scan_file(full, rel))
        return found
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            if f.endswith(".py"):
                full = os.path.join(d, f)
                found.extend(scan_file(full, os.path.relpath(full, root)))
    return found


def key(f: dict) -> str:
    return f"{f['file']}|{f['kind']}|{f['call']}"


def cron_programs_outside_repo() -> list[str]:
    """Programs the live crontab calls that are not in this repository
    (scripts/crontab_inventar.fremde_programme, #714). Empty if unavailable."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crontab_inventar.py")
    try:
        spec = importlib.util.spec_from_file_location("crontab_inventar", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return list(mod.fremde_programme())
    except Exception:  # noqa: BLE001 - the line says so below
        return []


def digest_lines(roots: dict[str, str], baseline: str | None = None,
                 extra_files: list[str] | None = None) -> list[str]:
    """For the 08:00 report: new module-level effects per deployed checkout,
    plus the cron programs that live outside the repository (no CI sees them)."""
    baseline = baseline or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "module_effects_baseline.json")
    try:
        allowed = {e["key"] for e in json.load(open(baseline)).get("entries", [])}
    except FileNotFoundError:
        allowed = set()
    out, items = [], []
    for name, root in roots.items():
        if not os.path.isdir(root):
            items.append(f"  {name}: Checkout fehlt ({root})")
            continue
        effects = [f for f in scan(root) if f["kind"] != "other"]
        new = [f for f in effects if key(f) not in allowed]
        out.append(f"{name} {len(new)} neu")
        items += [f"  neu: {name}/{f['file']}:{f['line']} {f['kind']} {f['call']}" for f in new[:5]]
    if extra_files is None:
        extra_files = cron_programs_outside_repo()
    ext_new = []
    for p in extra_files:
        if os.path.isfile(p):
            ext_new += [f for f in scan_file(p, p) if f["kind"] != "other" and key(f) not in allowed]
    out.append(f"Cron-Programme ausserhalb des Repos ({len(extra_files)}) {len(ext_new)} neu")
    items += [f"  neu: {f['file']}:{f['line']} {f['kind']} {f['call']}" for f in ext_new[:5]]
    return [f"Modulebene (Wirkung beim Laden) — {', '.join(out)}"] + items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="*", default=["."])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--baseline", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "module_effects_baseline.json"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    found = [dict(f, root=r) for r in a.roots for f in scan(r)]
    if a.json:
        print(json.dumps(found, indent=1))
        return 0
    if not a.check:
        for f in found:
            print(f"{f['file']}:{f['line']}  {f['kind']:<20} {f['call']}")
        return 0
    try:
        base = json.load(open(a.baseline))
    except FileNotFoundError:
        base = {"entries": []}
    allowed = {e["key"] for e in base.get("entries", [])}
    effects = [f for f in found if f["kind"] != "other"]
    new = [f for f in effects if key(f) not in allowed]
    found = effects
    for f in new:
        print(f"NEW {f['file']}:{f['line']}  {f['kind']}  {f['call']}")
    print(f"module-level effects: {len(found)} found, {len(found) - len(new)} in baseline, {len(new)} new")
    return 1 if new else 0


if __name__ == "__main__":
    raise SystemExit(main())
