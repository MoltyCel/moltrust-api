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

# --- ThreadWatch (post-time hook writes state["pinned"][repo#num]) ---
# scripts/threadwatch.py reads dynamic pins from this state file.
THREADWATCH_STATE = MOLTSTACK / "state" / "threadwatch.json"

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


# --- Discovery resilience (feed freshness + zero-intake alarm) ---
# A dead upstream must never look like "0 leads". Thresholds are named here and
# overridable via env so they can be tuned on the server without a code change.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# Written by scripts/discovery/discovery.py next to the feed.
DISCOVERY_HEALTH = Path(os.environ.get(
    "CONTENT_SCOUT_DISCOVERY_HEALTH", str(HOME / "moltycelbot" / "discovery_health.json")))
# Per-run intake stats + alarm throttle state (bounded history).
RUNS_STATE = Path(os.environ.get(
    "CONTENT_SCOUT_RUNS_STATE", str(MOLTSTACK / "state" / "content_scout_runs.json")))

# Discovery runs daily ~06:00 UTC, the scout at 06:30 and 17:30. 26h leaves one
# missed refresh's worth of slack before the feed counts as stale.
FEED_STALE_HOURS = _env_float("CONTENT_SCOUT_FEED_STALE_HOURS", 26)
# Zero intake (candidates==0 and classified==0) in EVERY run inside this window
# raises the alarm. Evening runs are normally 0 (the feed refreshes once a day),
# so the window must hold a morning run; the span/run-count floors below make
# sure a single quiet evening, or a burst of manual runs, never trips it.
# Between the 06:30/17:30 slots any span >= 20h is really >= 24h and contains a
# 06:00 refresh; 20 (not 24) keeps seconds of cron jitter from deferring the
# alarm by a whole run.
ZERO_INTAKE_WINDOW_HOURS = _env_float("CONTENT_SCOUT_ZERO_INTAKE_WINDOW_HOURS", 36)
ZERO_INTAKE_MIN_RUNS = int(_env_float("CONTENT_SCOUT_ZERO_INTAKE_MIN_RUNS", 3))
ZERO_INTAKE_MIN_SPAN_HOURS = _env_float("CONTENT_SCOUT_ZERO_INTAKE_MIN_SPAN_HOURS", 20)
# While an alarm condition persists: first alert immediately, then at most once
# per this many hours. A recovery message is sent when the condition clears.
ALARM_REALERT_HOURS = _env_float("CONTENT_SCOUT_ALARM_REALERT_HOURS", 24)
# Bounded run history.
RUNS_HISTORY_MAX = int(_env_float("CONTENT_SCOUT_RUNS_HISTORY_MAX", 120))
