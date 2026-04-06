#!/usr/bin/env python3
"""
MolTrust Concept Paper Review Pipeline
Perplexity (Research) + Gemini (Content) + GPT-4o (Devil's Advocate) + Claude (Synthesis)

Usage:
  python3 concept_review.py <paper.md> [--context "optional context"]
  python3 concept_review.py ~/moltstack/docs/mt_shopping_konzeptpapier.md --context "Target: Head of Product"
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import httpx

# ── Secrets ──────────────────────────────────────────────────────────────────

def load_secrets():
    secrets = {}
    secrets_file = Path.home() / ".moltrust_secrets"
    if secrets_file.exists():
        for line in secrets_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip()
    for key in ["OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
                "PERPLEXITY_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        if os.environ.get(key):
            secrets[key] = os.environ[key]
    return secrets

SECRETS = load_secrets()
ANTHROPIC_KEY  = SECRETS.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY     = SECRETS.get("OPENAI_API_KEY", "")
GEMINI_KEY     = SECRETS.get("GEMINI_API_KEY", "")
PERPLEXITY_KEY = SECRETS.get("PERPLEXITY_API_KEY", "")
TG_TOKEN       = SECRETS.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID     = SECRETS.get("TELEGRAM_CHAT_ID", "")

REVIEW_DIR = Path.home() / "moltstack" / "reviews"
REVIEW_DIR.mkdir(parents=True, exist_ok=True)

MAX_PAPER_LEN = 8000  # chars sent to each reviewer


# ── 1. Perplexity — Research & Literature ────────────────────────────────────

def review_perplexity(client: httpx.Client, paper: str, context: str) -> str:
    prompt = f"""You are a research assistant reviewing a concept paper.

Find relevant academic papers, industry reports, market data, and standards documents
that either support or challenge the claims in this paper.

For each finding:
- State the claim in the paper it relates to
- Provide the source (title, author/org, year, URL if available)
- Note if it supports, challenges, or adds nuance

Focus on: market data, technical standards (W3C, IETF), competitive landscape,
regulatory references, any factual claims that need verification.

{f"Context: {context}" if context else ""}

PAPER:
{paper[:MAX_PAPER_LEN]}

Format as structured markdown with source citations."""

    resp = client.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {PERPLEXITY_KEY}"},
        json={
            "model": "sonar",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.2,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ── 2. Gemini — Content & Argumentation ──────────────────────────────────────

def review_gemini(client: httpx.Client, paper: str, context: str) -> str:
    prompt = f"""You are a senior product strategist reviewing a concept paper.

Evaluate:
1. **Argument strength** — Are core claims well-supported?
2. **Logical gaps** — What is assumed but not proven?
3. **Internal consistency** — Do sections contradict each other?
4. **Audience fit** — Is the language appropriate for the target audience?
5. **Missing perspectives** — What important angles are not covered?

{f"Context: {context}" if context else ""}

PAPER:
{paper[:MAX_PAPER_LEN]}

Be specific and constructive. Format as structured markdown."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
    resp = client.post(
        url,
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2000},
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ── 3. GPT-4o — Devil's Advocate ────────────────────────────────────────────

def review_gpt4o(client: httpx.Client, paper: str, context: str) -> str:
    resp = client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a skeptical senior decision-maker. You have seen many vendor pitches and are hard to impress. Be tough but fair.",
                },
                {
                    "role": "user",
                    "content": f"""Review this concept paper as a tough critic.

Find weaknesses, overreaching claims, things that sound good but don't hold up.
Also note what IS genuinely strong.

{f"Context: {context}" if context else ""}

PAPER:
{paper[:MAX_PAPER_LEN]}

Format: markdown with sections for Genuine Strengths, Key Weaknesses,
Questions I Would Ask, and Deal-Breakers (if any).""",
                },
            ],
            "max_tokens": 1500,
            "temperature": 0.7,
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ── 4. Claude — Synthesis ────────────────────────────────────────────────────

def synthesize_claude(
    client: httpx.Client, paper: str,
    perplexity: str, gemini: str, gpt4o: str, context: str,
) -> str:
    resp = client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2000,
            "messages": [
                {
                    "role": "user",
                    "content": f"""You reviewed a concept paper with three specialist reviewers.
Synthesize their findings into actionable recommendations.

{f"Context: {context}" if context else ""}

ORIGINAL PAPER (excerpt):
{paper[:2000]}

PERPLEXITY (Research/Literature):
{perplexity[:1500]}

GEMINI (Content/Argumentation):
{gemini[:1500]}

GPT-4O (Devil's Advocate):
{gpt4o[:1500]}

Produce a synthesis:
1. **Consensus findings** — what all reviewers agree on
2. **Priority improvements** — top 3-5 changes to strengthen the paper
3. **Sources to add** — specific citations from Perplexity to incorporate
4. **Verdict:** PUBLISH / REVISE / MAJOR REWORK + one-sentence reason

Be direct and actionable. No filler.""",
                }
            ],
        },
        timeout=90.0,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


# ── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(message: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("(Telegram not configured)")
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message[:4096], "parse_mode": "HTML"},
            timeout=10.0,
        )
    except Exception as e:
        print(f"Telegram alert failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MolTrust Concept Paper Review Pipeline")
    parser.add_argument("paper", help="Path to concept paper (.md)")
    parser.add_argument("--context", default="", help="Context for reviewers")
    args = parser.parse_args()

    paper_path = Path(args.paper)
    if not paper_path.exists():
        print(f"Error: {paper_path} not found")
        sys.exit(1)

    paper_text = paper_path.read_text()
    label = paper_path.stem
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")

    errors = []
    results = {}

    with httpx.Client() as client:
        # 1. Perplexity
        print(f"Reviewing: {paper_path.name}")
        print("  -> Perplexity (Research)...", end=" ", flush=True)
        try:
            results["perplexity"] = review_perplexity(client, paper_text, args.context)
            print("done")
        except Exception as e:
            results["perplexity"] = f"ERROR: {e}"
            errors.append("Perplexity")
            print(f"FAILED: {e}")

        # 2. Gemini
        print("  -> Gemini (Content)...", end=" ", flush=True)
        try:
            results["gemini"] = review_gemini(client, paper_text, args.context)
            print("done")
        except Exception as e:
            results["gemini"] = f"ERROR: {e}"
            errors.append("Gemini")
            print(f"FAILED: {e}")

        # 3. GPT-4o
        print("  -> GPT-4o (Devil's Advocate)...", end=" ", flush=True)
        try:
            results["gpt4o"] = review_gpt4o(client, paper_text, args.context)
            print("done")
        except Exception as e:
            results["gpt4o"] = f"ERROR: {e}"
            errors.append("GPT-4o")
            print(f"FAILED: {e}")

        # 4. Claude Synthesis
        print("  -> Claude (Synthesis)...", end=" ", flush=True)
        try:
            results["synthesis"] = synthesize_claude(
                client, paper_text,
                results.get("perplexity", "unavailable"),
                results.get("gemini", "unavailable"),
                results.get("gpt4o", "unavailable"),
                args.context,
            )
            print("done")
        except Exception as e:
            results["synthesis"] = f"ERROR: {e}"
            errors.append("Claude")
            print(f"FAILED: {e}")

    # Build output
    output = f"""# Concept Review: {label}
**Generated:** {datetime.datetime.now().isoformat()}
**Context:** {args.context or "none"}
**Errors:** {", ".join(errors) if errors else "none"}

---

## Research & Literature (Perplexity)

{results.get("perplexity", "unavailable")}

---

## Content & Argumentation (Gemini)

{results.get("gemini", "unavailable")}

---

## Devil's Advocate (GPT-4o)

{results.get("gpt4o", "unavailable")}

---

## Synthesis & Recommendations (Claude)

{results.get("synthesis", "unavailable")}
"""

    output_file = REVIEW_DIR / f"{timestamp}_{label}_concept_review.md"
    output_file.write_text(output)
    print(f"\nSaved: {output_file}")

    # Telegram
    synth = results.get("synthesis", "")
    verdict_lines = [l for l in synth.split("\n") if "PUBLISH" in l or "REVISE" in l or "REWORK" in l]
    verdict = verdict_lines[0].strip()[:200] if verdict_lines else "Review complete"

    send_telegram(
        f"<b>Concept Review: {label}</b>\n\n"
        f"{verdict}\n\n"
        f"Errors: {', '.join(errors) if errors else 'none'}\n"
        f"File: <code>{output_file.name}</code>"
    )

    if errors:
        print(f"\nWarning: {len(errors)} reviewer(s) failed: {', '.join(errors)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
