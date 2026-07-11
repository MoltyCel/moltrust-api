"""Content-Scout configuration. Paths, model IDs, feeds, thresholds.

Model IDs pinned from the claude-api skill (docs.claude.com), not from memory:
  classify -> claude-haiku-4-5 ($1 / $5 per 1M in/out)
  draft    -> claude-opus-4-8  ($5 / $25 per 1M in/out)
Drafting uses Opus because a queued draft is public-facing copy and PASS volume
is low; classification uses Haiku because it runs over every candidate.
"""
import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
MOLTSTACK = HOME / "moltstack"

# --- Models + pricing (USD per 1M tokens) ---
MODEL_CLASSIFY = "claude-haiku-4-5"
MODEL_DRAFT = "claude-opus-4-8"
PRICING = {
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
    "claude-opus-4-8": {"in": 5.0, "out": 25.0},
}

# --- Feeds (verified read-only 2026-07-08; do NOT re-scan / re-implement discovery) ---
# discovery_candidates.json lives beside the repo (~/moltycelbot/), not inside it.
DISCOVERY_FEED = HOME / "moltycelbot" / "discovery_candidates.json"
# NewsScout's only file artifact (~/moltstack/data/news_sent_urls.json) is a dedup
# cache of *hashed* url_key()s — NOT URLs, NOT content. It is therefore unusable as
# a readable feed; the newsworthy content is Telegram-only. ingest() accepts only
# http(s) entries, so this yields 0 today (no scraping) and auto-works if news_scout
# is ever changed to persist real URLs + titles. See README "NewsScout status".
NEWSSCOUT_ARTIFACT = MOLTSTACK / "data" / "news_sent_urls.json"

# Safety cap so a first run over a large backlog can't draft unbounded.
MAX_CANDIDATES_PER_RUN = 60

# --- Guardrail docs, loaded at runtime (single source of truth; never inlined) ---
# WORKFLOW.md + CLAUDE.md live in moltrust-api (~/moltstack). The voice profiles
# (anti-KI-Sprech.md = negative side, my-voice-en.md = positive side) and
# website-deploy.md live in moltrust-web — the ONE canonical source — refreshed
# into a shallow clone by guardrails.ensure_web_docs(). The former
# ~/moltstack/docs/anti-KI-Sprech.md copy is retired (was diverging).
WEB_DOCS_CLONE = MOLTSTACK / "workers" / "content_scout" / ".webdocs"  # shallow moltrust-web
DOC_ANTI_KI = WEB_DOCS_CLONE / "anti-KI-Sprech.md"      # negative side (canonical: moltrust-web)
DOC_MY_VOICE_EN = WEB_DOCS_CLONE / "my-voice-en.md"     # positive side, English register
DOC_WORKFLOW = MOLTSTACK / "docs" / "WORKFLOW.md"
DOC_CLAUDE_MD = MOLTSTACK / "CLAUDE.md"
DOC_WEBSITE_DEPLOY_REL = "docs/website-deploy.md"

# --- Secrets ---
SECRETS_FILE = HOME / ".moltrust_secrets"
ANTHROPIC_KEY_FILE = HOME / ".anthropic_key"  # primary, mirrors trustscout.py

# --- DB ---
DB_HOST = "localhost"
DB_NAME = "moltstack"
DB_USER = "moltstack"

# --- HTTP identity ---
USER_AGENT = "MolTrust-ContentScout/0.1 (+https://moltrust.ch)"

# Low-balance gate: reuse the existing monitor's probe (scripts/check_credits.sh).
# The Anthropic API exposes no dollar balance, so "< $10" is not directly
# measurable; we probe API health and, on failure (quota/insufficient credit),
# fire the standard alert and run classify-only that cycle.
BALANCE_PROBE_MODEL = "claude-haiku-4-5"


def load_secrets() -> dict:
    out = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line[7:] if line.startswith("export ") else line
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def anthropic_key(secrets: dict) -> str:
    if ANTHROPIC_KEY_FILE.exists():
        k = ANTHROPIC_KEY_FILE.read_text(encoding="utf-8").strip()
        if k:
            return k
    return secrets.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
