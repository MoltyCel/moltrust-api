"""Anthropic wrapper: balance probe, classify (Haiku), draft (Opus). Cost accounting.

Uses the stable messages.create surface (server SDK is anthropic 0.79.0). Model
IDs come from config, which pins them from the claude-api skill.
"""
import json

import anthropic

from . import config

_spend = {"tokens_in": 0, "tokens_out": 0, "cost": 0.0}


def reset_spend():
    _spend.update(tokens_in=0, tokens_out=0, cost=0.0)


def spend() -> dict:
    return dict(_spend)


def _account(model: str, usage) -> None:
    p = config.PRICING[model]
    ti, to = usage.input_tokens, usage.output_tokens
    _spend["tokens_in"] += ti
    _spend["tokens_out"] += to
    _spend["cost"] += ti / 1e6 * p["in"] + to / 1e6 * p["out"]


def make_client(api_key: str) -> "anthropic.Anthropic":
    return anthropic.Anthropic(api_key=api_key)


def balance_ok(client) -> bool:
    """Reuse the existing monitor's approach (scripts/check_credits.sh): a tiny
    ping. Failure (quota/insufficient credit/auth) => unhealthy => classify-only."""
    try:
        client.messages.create(
            model=config.BALANCE_PROBE_MODEL, max_tokens=4,
            messages=[{"role": "user", "content": "ok"}],
        )
        return True
    except Exception:
        return False


def classify(client, system: str, candidate_text: str) -> dict:
    """Return {'verdict': PASS|WATCH|DROP, 'reason': str}. Cheap, over all candidates."""
    resp = client.messages.create(
        model=config.MODEL_CLASSIFY, max_tokens=400, system=system,
        messages=[{"role": "user", "content": candidate_text}],
    )
    _account(config.MODEL_CLASSIFY, resp.usage)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
        verdict = str(data.get("verdict", "DROP")).upper()
        if verdict not in ("PASS", "WATCH", "DROP"):
            verdict = "DROP"
        return {"verdict": verdict, "reason": str(data.get("reason", ""))[:500]}
    except Exception:
        return {"verdict": "DROP", "reason": "classifier output unparseable"}


def point(client, system: str, user: str) -> tuple[str, str]:
    """Return (one_line_point, model_used). Cheap (Haiku), PASS items only — a single
    factual, primary-source-checkable sentence naming the hook. NOT a composed comment;
    the lead model never drafts full copy. First line only, hard-capped."""
    resp = client.messages.create(
        model=config.MODEL_CLASSIFY, max_tokens=200, system=system,
        messages=[{"role": "user", "content": user}],
    )
    _account(config.MODEL_CLASSIFY, resp.usage)
    txt = "".join(b.text for b in resp.content if b.type == "text").strip()
    return (txt.split("\n", 1)[0].strip()[:400] or "(no point)"), config.MODEL_CLASSIFY
