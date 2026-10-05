"""Load guardrail docs at runtime (single source of truth — never inline text).

WORKFLOW.md + CLAUDE.md live in moltrust-api (~/moltstack). The voice profiles
(anti-KI-Sprech.md negative side, my-voice-en.md positive side) and
website-deploy.md live in moltrust-web; we keep a shallow clone and read the
repo's own files so the drafter always sees the current, single-source version.
"""
import os
import subprocess

from . import config


REMOTE = "https://github.com/MoltyCel/moltrust-web.git"

# The remote URL carries no credential. It used to: the clone was created as
# `https://MoltyCel:<pat>@github.com/...`, and `git remote set-url` wrote the
# current token back on every refresh. That put a live PAT in
# .webdocs/.git/config at mode 664 — world-readable, on a host with a second
# human account — and it stayed there after the token was superseded. Found on
# 2026-10-05 while chasing a stale doc mirror: the stored token was not the
# current MOLTYCEL_GH_TOKEN any more and still carried push and admin on both
# private repos.
#
# So the token travels in the environment, where /proc/<pid>/environ is readable
# by the owner alone, and git asks for it through a credential helper. The
# helper's own text appears in the process arguments; the secret does not. A
# helper is only consulted for a URL without credentials, which is why the
# migration above has to strip the stored one.
_HELPER = ('!f() { printf "username=MoltyCel\\npassword=%s\\n" '
           '"$MOLTRUST_GIT_TOKEN"; }; f')


def _git(token: str, args: list[str], timeout: int):
    env = dict(os.environ, MOLTRUST_GIT_TOKEN=token or "")
    return subprocess.run(["git", "-c", f"credential.helper={_HELPER}", *args],
                          check=False, timeout=timeout, capture_output=True,
                          env=env)


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[guardrail doc missing: {path}]"


def ensure_web_docs(gh_token: str) -> None:
    """Shallow-clone or hard-refresh moltrust-web main so the voice profiles and
    website-deploy.md are current.

    The clone is a read-only mirror (we never commit into it), so we fetch the
    latest main and reset --hard onto it. A plain `git pull` fails on a shallow
    clone once local and remote history "diverge"
    ("fatal: Need to specify how to reconcile divergent branches"), which silently
    froze the mirror at its original commit and starved newer docs.
    """
    clone = config.WEB_DOCS_CLONE
    try:
        if (clone / ".git").exists():
            # Drop any credential a previous version stored in the URL, then
            # hard-mirror main. set-url also performs the migration: one refresh
            # and the token is out of .git/config for good.
            _git(gh_token, ["-C", str(clone), "remote", "set-url", "origin", REMOTE], 30)
            _git(gh_token, ["-C", str(clone), "fetch", "--quiet", "--depth", "1",
                            "origin", "main"], 60)
            _git(gh_token, ["-C", str(clone), "reset", "--hard", "--quiet",
                            "FETCH_HEAD"], 60)
        else:
            clone.parent.mkdir(parents=True, exist_ok=True)
            _git(gh_token, ["clone", "--quiet", "--depth", "1", REMOTE, str(clone)], 120)
    except Exception:
        pass  # a stale/missing clone degrades to the "missing" marker, not a crash


def load_all(gh_token: str) -> dict:
    """Return {name: text} for every guardrail doc the drafter needs."""
    ensure_web_docs(gh_token)
    return {
        "anti_ki_sprech": _read(config.DOC_ANTI_KI),
        "my_voice_en": _read(config.DOC_MY_VOICE_EN),
        "workflow": _read(config.DOC_WORKFLOW),
        "claude_md": _read(config.DOC_CLAUDE_MD),
        "website_deploy": _read(config.WEB_DOCS_CLONE / config.DOC_WEBSITE_DEPLOY_REL),
    }
