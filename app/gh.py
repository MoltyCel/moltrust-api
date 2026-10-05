"""The GitHub token, under one name.

Until 2026-10-05 two names held two different tokens. `GH_TOKEN` was a
fine-grained PAT with push and admin on both private repos; it also sat in
clear text at mode 664 inside the doc mirror's `.git/config`, on a host with a
second human account, because `ensure_web_docs` wrote it back into the remote
URL on every refresh. `MOLTYCEL_GH_TOKEN` was the console's own.

Seven places read `GH_TOKEN` and each spelled the lookup itself, so "which
token does this path use" had seven answers. Two of those paths swallow a
failed read, which is why the question mattered: after the revocation they
would have kept reporting health.

One name, read at call time, in one place.
"""
from __future__ import annotations

import os
import pathlib

NAME = "MOLTYCEL_GH_TOKEN"
SECRETS = "~/.moltrust_secrets"


def token() -> str:
    """The token, from the environment or the secrets file. "" when absent.

    The file is read as a fallback because several callers are crons that do
    not `set -a`, and a token that is present but unreadable to the process is
    the same silent failure under a different name.
    """
    tok = os.environ.get(NAME, "").strip()
    if tok:
        return tok
    path = pathlib.Path(os.path.expanduser(SECRETS))
    try:
        for line in path.read_text().splitlines():
            if line.startswith(f"{NAME}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def require(who: str) -> str:
    """The token or a RuntimeError naming the caller. For paths that must not
    continue without it — as opposed to the two that used to continue quietly."""
    tok = token()
    if not tok:
        raise RuntimeError(
            f"{who}: {NAME} is not set. One key, one name — since 2026-10-05 "
            f"there is no GH_TOKEN to fall back to.")
    return tok
