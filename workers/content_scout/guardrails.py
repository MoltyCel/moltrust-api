"""Load guardrail docs at runtime (single source of truth — never inline text).

anti-KI-Sprech.md, WORKFLOW.md, CLAUDE.md live in moltrust-api (~/moltstack).
website-deploy.md lives in moltrust-web; we keep a shallow clone and read the
repo's own file so the drafter always sees the current version.
"""
import subprocess

from . import config


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"[guardrail doc missing: {path}]"


def ensure_web_docs(gh_token: str) -> None:
    """Shallow-clone or refresh moltrust-web so website-deploy.md is current."""
    clone = config.WEB_DOCS_CLONE
    url = f"https://MoltyCel:{gh_token}@github.com/MoltyCel/moltrust-web.git"
    try:
        if (clone / ".git").exists():
            subprocess.run(["git", "-C", str(clone), "pull", "--quiet", "--depth", "1"],
                           check=False, timeout=60, capture_output=True)
        else:
            clone.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--quiet", "--depth", "1", url, str(clone)],
                           check=False, timeout=120, capture_output=True)
    except Exception:
        pass  # a stale/missing clone degrades to the "missing" marker, not a crash


def load_all(gh_token: str) -> dict:
    """Return {name: text} for every guardrail doc the drafter needs."""
    ensure_web_docs(gh_token)
    return {
        "anti_ki_sprech": _read(config.DOC_ANTI_KI),
        "workflow": _read(config.DOC_WORKFLOW),
        "claude_md": _read(config.DOC_CLAUDE_MD),
        "website_deploy": _read(config.WEB_DOCS_CLONE / config.DOC_WEBSITE_DEPLOY_REL),
    }
