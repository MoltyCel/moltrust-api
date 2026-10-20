#!/usr/bin/env python3
"""Keep the voice-gate document mirror current, on its own schedule.

`agents/voice_gate.py` enforces the rules out of
`workers/content_scout/.webdocs/`, a shallow clone of moltrust-web. Nothing
refreshed that clone on a tick of its own: it was pulled as a side effect of
the content_scout pipeline, so the rules the gate enforces aged whenever that
worker paused, and nothing said so.

Found on 2026-10-05: moltrust-web had merged #267, which rewrote a section of
pre-send-scan.md, and the mirror still stood one commit back. The rule *count*
was identical (26 against 26), so only a content comparison could see it. The
deploy check caught it, which is late — a deploy is not a tick.

    python3 scripts/refresh_doc_mirror.py --check    # read-only, exit 1 if stale
    python3 scripts/refresh_doc_mirror.py            # fetch and hard-reset

Idempotent: a refresh on a current mirror fetches and resets onto the same
commit and reports "unchanged". The fingerprints are the same ones
`scripts/deploy_verify.py` compares, so both answer the same question.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.content_scout import config, guardrails


def _token() -> str:
    tok = os.environ.get("MOLTYCEL_GH_TOKEN", "")
    if tok:
        return tok
    # The secrets file is the single source; read it rather than failing on a
    # cron that did not `set -a`.
    path = os.path.expanduser("~/.moltrust_secrets")
    if os.path.exists(path):
        for line in open(path):
            if line.startswith("MOLTYCEL_GH_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def head(clone) -> str:
    r = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                       capture_output=True, text=True, timeout=30)
    return (r.stdout or "").strip()[:12]


def remote_main(token: str, clone) -> str:
    """What main points at now. The remote is asked, not the local checkout of
    moltrust-web, which only moves when the website deploys."""
    r = guardrails._git(token, ["-C", str(clone), "ls-remote", guardrails.REMOTE,
                               "refs/heads/main"], 60)
    out = (r.stdout.decode() if isinstance(r.stdout, bytes) else r.stdout) or ""
    return out.split()[0][:12] if out.split() else ""


def state() -> tuple[str, str, bool]:
    clone = config.WEB_DOCS_CLONE
    token = _token()
    if not (clone / ".git").exists():
        return "", remote_main(token, clone.parent), False
    local, remote = head(clone), remote_main(token, clone)
    return local, remote, bool(local and remote and local == remote)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="read-only: report staleness, change nothing")
    a = ap.parse_args(argv)

    before, remote, current = state()
    if a.check:
        if not before:
            print("doc mirror: not cloned yet")
            return 1
        if not remote:
            # Not knowing is not the same as being fine.
            print(f"doc mirror: at {before}, remote main unreadable — unproven")
            return 1
        print(f"doc mirror: {before}, main {remote} — "
              f"{'current' if current else 'STALE'}")
        return 0 if current else 1

    guardrails.ensure_web_docs(_token())
    after = head(config.WEB_DOCS_CLONE)
    if after == before:
        print(f"doc mirror unchanged at {after}")
    else:
        print(f"doc mirror {before or '(none)'} -> {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
