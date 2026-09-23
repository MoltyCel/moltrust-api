#!/usr/bin/env python3
"""Who can write to Moltbook under one of our accounts, and who is scheduled to.

    python3 scripts/moltbook_writers.py            # the inventory
    python3 scripts/moltbook_writers.py --check    # exit 1 on an undeclared writer

On 2026-09-21 agents/ambassador.py started replying on Moltbook under
moltrust-agent. On 2026-09-23 a second reply path was switched on in
moltbook/heartbeat.py, in the belief that nothing else answered. Two agents
would have answered the same person twice under one name. It was caught by
reading a comment list and noticing nine entries nobody could account for,
which is luck rather than a control.

This is the control. It reads three surfaces — the repository, the crontab and
the systemd units — works out which files can write to Moltbook, which of
those are scheduled to run, and compares that against the list of writers we
have agreed to. A scheduled writer that is not on the list is an alarm.

"Can write" means all three of: a reference to one of our Moltbook keys, a
POST to a Moltbook URL, and no marker saying otherwise. Reading is not
writing, so scripts/moltbook_stats.py and scripts/sm_kpis.py are expected to
be absent from the result.

Exit 0 clean, 1 an undeclared scheduled writer, 2 the inventory could not be
taken. Read-only: nothing here starts, stops or edits anything.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess  # nosec B404 - runs crontab and systemctl with fixed argument lists
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

MOLTBOOK_HOST = "moltbook.com"
SECRETS_FILE = os.path.expanduser("~/.moltrust_secrets")

# Key names are derived, never typed. The first version of this listed two by
# hand and missed MOLTGUARD_MOLTBOOK_KEY, so agents/moltguard.py — a writer
# everyone knew about — did not appear in an inventory whose whole job is to
# find writers nobody knew about.
KEY_NAME_PATTERN = re.compile(r"\bMOLT(?:BOOK|GUARD)[A-Z0-9_]*(?:KEY|TOKEN)\b")

# A URL assigned to a name, so a post can be traced to Moltbook rather than to
# whatever else the file talks to. agents/pr_monitor.py holds a Moltbook base
# and a Moltbook key and posts to Telegram; counting it as a writer would
# train people to ignore the alarm.
URL_ASSIGN = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*[\"\']https?://[^\"\']*"
                        + MOLTBOOK_HOST.replace(".", r"\.") , re.M)
HELPER_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w*moltbook\w*)\s*\(", re.M | re.I)


def key_names() -> set[str]:
    """Every Moltbook-ish secret name, from the secrets file and by shape."""
    names = set()
    try:
        for line in open(SECRETS_FILE, encoding="utf-8"):
            name = line.split("=", 1)[0].strip()
            if KEY_NAME_PATTERN.fullmatch(name):
                names.add(name)
    except OSError:
        pass
    return names


def posts_to_moltbook(text: str, extra_names: set[str]) -> bool:
    """Whether a POST in this file is aimed at Moltbook.

    A post counts when its call text names a constant holding a Moltbook URL,
    or when it goes through a helper this file defines whose name says
    moltbook, or when the literal host is in the call itself.
    """
    targets = set(m.group(1) for m in URL_ASSIGN.finditer(text)) | extra_names
    helpers = set(m.group(1) for m in HELPER_DEF.finditer(text))
    for call in re.finditer(r"(?:^|[^@\w.])(\w+(?:\.\w+)*)\s*\(([^)]{0,200})", text, re.M):
        name, args = call.group(1), call.group(2)
        # Route decorators are not client calls. app/main.py declares
        # @app.post("/auth/moltbook") and posts to nobody; counting a FastAPI
        # route as an outbound writer would put the whole API in the alarm.
        root = name.split(".")[0]
        if root in {"app", "router", "api", "blueprint"}:
            continue
        is_client_post = re.fullmatch(r"(?:requests|httpx|client|session|_client|self\.client)\.post",
                                      name) is not None
        if not (is_client_post or name in helpers):
            continue
        if MOLTBOOK_HOST in args:
            return True
        if any(re.search(rf"\b{re.escape(t)}\b", args) for t in targets if t):
            return True
        if name in helpers:
            # A helper named for Moltbook that itself reaches the host.
            body = text[call.start():call.start() + 2000]
            if MOLTBOOK_HOST in body or any(t in body for t in targets if t):
                return True
    return False

# The writers we have agreed to, and what each is for. A file that can write
# and is scheduled but is missing here is the thing this script exists to
# find. Adding a line is a deliberate act; it belongs in a pull request with a
# reason, not in a hotfix.
DECLARED = {
    "agents/ambassador.py": "replies on our own threads, every 30 min; the single reply path",
    "agents/moltbook_poster.py": "one post a day, 09:00 UTC",
    "moltbook/heartbeat.py": "upvotes; its reply path is off unless MOLTBOOK_REPLY_ONLY is set",
    "agents/moltguard.py": "moltguard_v1 posts to m/security",
    "agents/auditor.py": "security-scan summary, Mondays 10:00 UTC — found by this "
                         "script on its first run, declared 2026-09-23. Note it "
                         "lands an hour after the daily post, so Mondays carry two.",
}

SEARCH_SUFFIXES = (".py", ".sh", ".ts", ".js")
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}


def capable_writers() -> dict[str, list[str]]:
    """Files that could post to Moltbook with one of our keys."""
    found: dict[str, list[str]] = {}
    keys = key_names()
    for path in REPO.rglob("*"):
        if path.is_dir() or path.suffix not in SEARCH_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if MOLTBOOK_HOST not in text:
            continue
        used = {k for k in keys if k in text} | set(KEY_NAME_PATTERN.findall(text))
        if not used:
            continue
        if not posts_to_moltbook(text, set()):
            continue
        rel = str(path.relative_to(REPO))
        if rel.startswith("tests/"):
            continue
        found[rel] = sorted(used)
    return found


def _run(args: list[str]) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20,  # nosec B603 - fixed argv, no shell
                             check=False)
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def scheduled() -> dict[str, str]:
    """Every file named by a crontab line or a systemd unit, and where."""
    where: dict[str, str] = {}

    for line in _run(["crontab", "-l"]).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for match in re.finditer(r"[\w./-]+\.(?:py|sh)", stripped):
            name = match.group(0)
            rel = name.split("moltstack/")[-1].lstrip("./")
            where.setdefault(rel, "crontab")

    units = _run(["systemctl", "list-units", "--type=service", "--all", "--no-legend"])
    for line in units.splitlines():
        unit = line.strip().split()[0] if line.strip() else ""
        if not unit.endswith(".service"):
            continue
        body = _run(["systemctl", "cat", unit])
        if not body:
            continue
        for match in re.finditer(r"[\w./-]+\.(?:py|sh)", body):
            rel = match.group(0).split("moltstack/")[-1].lstrip("./")
            if rel in where and where[rel] == "crontab":
                where[rel] += f" + {unit}"
            else:
                where.setdefault(rel, unit)
    return where


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when a scheduled writer is not declared")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        capable = capable_writers()
        runs = scheduled()
    except Exception as exc:  # noqa: BLE001 - the message is the output
        print(f"moltbook_writers: {exc}", file=sys.stderr)
        return 2

    if not capable:
        # A grep that finds nothing is either a clean repository or a broken
        # pattern, and the second is far likelier here.
        print("moltbook_writers: no writer found at all — the patterns are "
              "probably wrong, refusing to report an all-clear", file=sys.stderr)
        return 2

    rows = []
    for rel in sorted(capable):
        run_by = runs.get(rel) or runs.get(os.path.basename(rel), "")
        rows.append({
            "file": rel,
            "keys": capable[rel],
            "scheduled_by": run_by,
            "declared": rel in DECLARED,
            "purpose": DECLARED.get(rel, ""),
        })

    undeclared = [r for r in rows if r["scheduled_by"] and not r["declared"]]
    latent = [r for r in rows if not r["scheduled_by"] and not r["declared"]]

    if args.json:
        print(json.dumps({"rows": rows, "undeclared": [r["file"] for r in undeclared]}))
        return 1 if (args.check and undeclared) else 0

    print(f"Moltbook-Schreiber: {len(rows)} schreibfaehig, "
          f"{sum(1 for r in rows if r['scheduled_by'])} davon eingeplant")
    for r in rows:
        mark = "ok " if r["declared"] else "NEU"
        run = r["scheduled_by"] or "nicht eingeplant"
        print(f"  [{mark}] {r['file']:<34} {run}")
        if r["purpose"]:
            print(f"        {r['purpose']}")

    if undeclared:
        print(f"\nALARM: {len(undeclared)} eingeplante Schreiber stehen nicht in DECLARED:")
        for r in undeclared:
            print(f"  {r['file']}  ({r['scheduled_by']})")
        print("Entweder gehoert der Schreiber dorthin — dann per PR eintragen, mit Grund —")
        print("oder er soll nicht schreiben. Ein zweiter Antwortpfad neben dem Ambassador")
        print("beantwortet dieselbe Person zweimal unter einem Namen.")
    if latent:
        print(f"\n{len(latent)} schreibfaehig, aber nicht eingeplant und nicht erklaert.")
        print("Diese Liste faengt bewusst zu weit: sie meldet auch Dateien, die eine")
        print("Moltbook-URL und irgendeinen POST enthalten, ohne dass beides zusammen")
        print("gehoert. Der Alarm oben ist die belastbare Haelfte.")
        for r in latent:
            print(f"  {r['file']}")
        print("Kein Alarm, weil nichts sie startet. Wer eine Cron-Zeile dafuer anlegt,")
        print("loest beim naechsten Lauf einen aus.")

    return 1 if (args.check and undeclared) else 0


if __name__ == "__main__":
    sys.exit(main())
