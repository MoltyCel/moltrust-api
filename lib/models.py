"""Central Claude model IDs for MolTrust agents and scripts.

A retired model ID answers every request with HTTP 404, so the ID must not sit
hardcoded at the call site: one knob has to reach every caller. Resolution order
per entry is environment variable, then ~/.moltrust_secrets, then the default
below. A retired default must fail loudly at the call site, not degrade silently.

Defaults are pinned from the claude-api skill (docs.claude.com), not from memory.
The synthesis model and, since 2026-10-19, the four cross-review models live
here. The Haiku and Opus call sites still carry their own IDs and are a
separate step.
"""
import os
from pathlib import Path

SECRETS_FILE = Path.home() / ".moltrust_secrets"

# Multi-reviewer synthesis: agents/ai_review.py, agents/ai_review_v2.py,
# scripts/concept_review.py. Replaces claude-sonnet-4-20250514, which retired
# on 2026-06-15.
DEFAULT_SYNTHESIS_MODEL = "claude-sonnet-4-6"


def _from_secrets(key: str) -> str:
    """Read one key from ~/.moltrust_secrets. Returns "" when absent."""
    if not SECRETS_FILE.exists():
        return ""
    for line in SECRETS_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip()
    return ""


def resolve(key: str, default: str) -> str:
    """Model ID for `key`: env var wins, then secrets file, then `default`."""
    return os.environ.get(key) or _from_secrets(key) or default


SYNTHESIS_MODEL = resolve("SYNTHESIS_MODEL", DEFAULT_SYNTHESIS_MODEL)


# Cross-review models: agents/ai_review.py, agents/ai_review_v2.py,
# scripts/concept_review.py. Chosen on 2026-10-19 from each provider's model
# list as the strongest generally available text model: gpt-6-astra (OpenAI
# flagship), gemini-4-argon (stable, Gemini flagship), sonar-reasoning-pro
# (strongest synchronous Sonar model), mistral-medium-2604 (Mistral Medium
# 3.5; no Large model is listed). Replaces gpt-5, gpt-4o,
# gemini-3.1-pro-preview, gemini-2.5-flash, sonar-pro, sonar and
# mistral-large-latest.
DEFAULT_OPENAI_REVIEW_MODEL = "gpt-6-astra"
DEFAULT_GEMINI_REVIEW_MODEL = "gemini-4-argon"
DEFAULT_PERPLEXITY_REVIEW_MODEL = "sonar-reasoning-pro"
DEFAULT_MISTRAL_REVIEW_MODEL = "mistral-medium-2604"

OPENAI_REVIEW_MODEL = resolve("OPENAI_REVIEW_MODEL", DEFAULT_OPENAI_REVIEW_MODEL)
GEMINI_REVIEW_MODEL = resolve("GEMINI_REVIEW_MODEL", DEFAULT_GEMINI_REVIEW_MODEL)
PERPLEXITY_REVIEW_MODEL = resolve("PERPLEXITY_REVIEW_MODEL", DEFAULT_PERPLEXITY_REVIEW_MODEL)
MISTRAL_REVIEW_MODEL = resolve("MISTRAL_REVIEW_MODEL", DEFAULT_MISTRAL_REVIEW_MODEL)
