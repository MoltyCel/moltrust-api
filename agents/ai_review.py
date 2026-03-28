#!/usr/bin/env python3
"""
MolTrust Multi-AI Review Pipeline
Sendet MD-Dokumente an OpenAI + Gemini, synthetisiert via Claude, Telegram-Alert.

Usage:
  python3 ai_review.py <path/to/document.md> [--label "Security Konzept v1"] [--mode security|technical|whitepaper]
"""

import asyncio
import argparse
import os
import sys
import json
import datetime
import httpx
from pathlib import Path

# ── Secrets laden ────────────────────────────────────────────────────────────
SECRETS_FILE = Path.home() / ".moltrust_secrets"

def load_secrets():
    secrets = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                secrets[k.strip()] = v.strip()
    # Env vars haben Vorrang
    for key in ["OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        if os.environ.get(key):
            secrets[key] = os.environ[key]
    return secrets

SECRETS = load_secrets()

OPENAI_KEY     = SECRETS.get("OPENAI_API_KEY", "")
GEMINI_KEY     = SECRETS.get("GEMINI_API_KEY", "")
ANTHROPIC_KEY  = SECRETS.get("ANTHROPIC_API_KEY", "")
TG_TOKEN       = SECRETS.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID     = SECRETS.get("TELEGRAM_CHAT_ID", "")

OUTPUT_DIR = Path.home() / "moltstack" / "reviews"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Review-Prompts je Modus ──────────────────────────────────────────────────
REVIEW_PROMPTS = {
    "security": """Du bist ein unabhängiger Security-Reviewer für dezentrale KI-Infrastruktur.
Analysiere das folgende Dokument ausschließlich aus Security-Perspektive.

Strukturiere deine Antwort exakt so:
## 1. Kritische Schwachstellen
## 2. Mittlere Risiken
## 3. Stärken / bereits gut gelöst
## 4. Konkrete Empfehlungen (priorisiert)
## 5. Fehlende Aspekte

Sei präzise, technisch, keine Marketing-Sprache.""",

    "technical": """Du bist ein unabhängiger technischer Reviewer für dezentrale Protokolle und W3C-Standards.
Analysiere das Dokument auf technische Korrektheit, Vollständigkeit und Implementierbarkeit.

Strukturiere deine Antwort exakt so:
## 1. Technische Korrektheit
## 2. Lücken / offene Fragen
## 3. Implementierungs-Risiken
## 4. Verbesserungsvorschläge
## 5. Vergleich mit existierenden Standards (DIF, W3C, IETF)

Sei präzise, keine Pauschalaussagen.""",

    "whitepaper": """Du bist ein unabhängiger Reviewer für technische Whitepapers im Web3/AI-Infrastruktur-Bereich.
Analysiere das Whitepaper auf Argumentation, Klarheit, Marktpositionierung und wissenschaftliche Fundierung.

Strukturiere deine Antwort exakt so:
## 1. Kernthese — klar und überzeugend?
## 2. Schwache Argumentationsketten
## 3. Fehlende Referenzen / Belege
## 4. Marktpositionierung — realistisch?
## 5. Empfehlungen für nächste Version

Konstruktiv aber direkt."""
}

SYNTHESIS_PROMPT = """Du bist Lead-Reviewer bei MolTrust. Du hast Reviews von OpenAI (GPT-4o) und Google (Gemini) zu demselben Dokument erhalten.

Deine Aufgabe: Synthetisiere beide Reviews in ein klares Entscheidungsdokument für den Gründer.

Strukturiere exakt so:

# Synthesis Review — {label}
**Datum:** {date}
**Reviewer:** GPT-4o + Gemini 1.5 Pro → Synthese via Claude

---

## 🔴 Konsens: Kritische Punkte
(Punkte, die BEIDE Reviews als Problem sehen)

## 🟡 Divergenz: Unterschiedliche Einschätzungen
(Wo GPT-4o und Gemini sich widersprechen — mit kurzer Bewertung wer Recht hat)

## 🟢 Konsens: Stärken
(Punkte, die BEIDE positiv bewerten)

## 📋 Priorisierte Aktionsliste
(Konkrete TODOs, nach Dringlichkeit sortiert — max. 10 Items)

## ✅ Freigabe-Empfehlung
Klares Votum: FREIGEBEN / ÜBERARBEITEN / GRUNDLEGEND ÜBERDENKEN — mit 2-Satz-Begründung.

---

### GPT-4o Review (Zusammenfassung)
{openai_summary}

### Gemini Review (Zusammenfassung)
{gemini_summary}
"""

# ── API Calls ────────────────────────────────────────────────────────────────

async def call_openai(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """GPT-4o Review Call"""
    if not OPENAI_KEY:
        return {"model": "GPT-4o", "content": "ERROR: OPENAI_API_KEY nicht gesetzt", "error": True}

    system_prompt = REVIEW_PROMPTS[mode]
    payload = {
        "model": "gpt-4o",
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Hier ist das Dokument zur Review:\n\n{document}"}
        ]
    }

    try:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=120
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", "?")
        return {"model": "GPT-4o", "content": content, "tokens": tokens, "error": False}
    except Exception as e:
        return {"model": "GPT-4o", "content": f"ERROR: {e}", "error": True}


async def call_gemini(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """Gemini 1.5 Pro Review Call"""
    if not GEMINI_KEY:
        return {"model": "Gemini 1.5 Pro", "content": "ERROR: GEMINI_API_KEY nicht gesetzt", "error": True}

    system_prompt = REVIEW_PROMPTS[mode]
    combined_prompt = f"{system_prompt}\n\nHier ist das Dokument zur Review:\n\n{document}"

    payload = {
        "contents": [{"parts": [{"text": combined_prompt}]}],
        "generationConfig": {"maxOutputTokens": 4000, "temperature": 0.3}
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"

    try:
        resp = await client.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        content = data["candidates"][0]["content"]["parts"][0]["text"]
        tokens = data.get("usageMetadata", {}).get("totalTokenCount", "?")
        return {"model": "Gemini 1.5 Pro", "content": content, "tokens": tokens, "error": False}
    except Exception as e:
        return {"model": "Gemini 1.5 Pro", "content": f"ERROR: {e}", "error": True}


async def call_claude_synthesis(client: httpx.AsyncClient, openai_result: dict,
                                 gemini_result: dict, label: str) -> str:
    """Claude synthetisiert beide Reviews"""
    if not ANTHROPIC_KEY:
        return "ERROR: ANTHROPIC_API_KEY nicht gesetzt"

    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    user_prompt = SYNTHESIS_PROMPT.format(
        label=label,
        date=date_str,
        openai_summary=openai_result["content"],
        gemini_summary=gemini_result["content"]
    )

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 3000,
        "messages": [{"role": "user", "content": user_prompt}]
    }

    try:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=120
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]
    except Exception as e:
        return f"ERROR Synthesis: {e}"


async def send_telegram(client: httpx.AsyncClient, message: str):
    """Telegram Notification"""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️  Telegram nicht konfiguriert — kein Alert gesendet")
        return
    try:
        await client.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=30
        )
    except Exception as e:
        print(f"⚠️  Telegram Alert fehlgeschlagen: {e}")


# ── Main Pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(doc_path: Path, label: str, mode: str, context: str = ""):
    document = doc_path.read_text(encoding="utf-8")
    if context:
        document = f"## Kontext aus vorherigen Reviews\n\n{context}\n\n---\n\n## Dokument zur Review\n\n{document}"
    word_count = len(document.split())
    char_count = len(document)

    print(f"\n{'='*60}")
    print(f"🚀 MolTrust AI Review Pipeline")
    print(f"   Dokument : {doc_path.name} ({word_count} Wörter)")
    print(f"   Label    : {label}")
    print(f"   Modus    : {mode}")
    print(f"{'='*60}\n")

    # Dokument auf 15k chars kürzen falls nötig (Token-Limit Schutz)
    if char_count > 25000:
        document = document[:25000] + "\n\n[... Dokument gekürzt für Review ...]"
        print(f"⚠️  Dokument auf 25.000 Zeichen gekürzt (war {char_count})\n")

    async with httpx.AsyncClient() as client:
        # 1. Parallel Reviews
        print("📤 Sende an GPT-4o + Gemini (parallel)...")
        openai_task = call_openai(client, document, mode)
        gemini_task = call_gemini(client, document, mode)
        openai_result, gemini_result = await asyncio.gather(openai_task, gemini_task)

        print(f"   GPT-4o   : {'✅' if not openai_result['error'] else '❌'} ({openai_result.get('tokens', '?')} Tokens)")
        print(f"   Gemini   : {'✅' if not gemini_result['error'] else '❌'} ({gemini_result.get('tokens', '?')} Tokens)")

        # 2. Synthesis via Claude
        print("\n🧠 Synthetisiere via Claude...")
        synthesis = await call_claude_synthesis(client, openai_result, gemini_result, label)
        print("   Synthesis : ✅")

        # 3. Output-File schreiben
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = label.replace(" ", "_").replace("/", "-")[:40]
        output_path = OUTPUT_DIR / f"{ts}_{safe_label}_review.md"

        full_output = f"""# AI Review: {label}
**Generiert:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")}
**Quelle:** {doc_path.name}
**Modus:** {mode}

---

{synthesis}

---

## Raw Reviews

<details>
<summary>GPT-4o Raw Review</summary>

{openai_result['content']}

</details>

<details>
<summary>Gemini 1.5 Pro Raw Review</summary>

{gemini_result['content']}

</details>
"""
        output_path.write_text(full_output, encoding="utf-8")
        print(f"\n💾 Gespeichert: {output_path}")

        # 4. Telegram Alert
        errors = [r["model"] for r in [openai_result, gemini_result] if r["error"]]
        status = "✅ Vollständig" if not errors else f"⚠️ Fehler bei: {', '.join(errors)}"

        tg_msg = (
            f"🔍 *AI Review abgeschlossen*\n"
            f"Label: `{label}`\n"
            f"Modus: `{mode}`\n"
            f"Status: {status}\n"
            f"File: `{output_path.name}`\n\n"
            f"Freigabe-Empfehlung folgt im Dokument."
        )
        await send_telegram(client, tg_msg)
        print("📱 Telegram Alert gesendet\n")

    print(f"{'='*60}")
    print(f"✅ Review abgeschlossen: {output_path.name}")
    print(f"{'='*60}\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="MolTrust Multi-AI Review Pipeline")
    parser.add_argument("document", help="Pfad zum MD-Dokument")
    parser.add_argument("--label", default="", help="Bezeichnung für den Review")
    parser.add_argument("--mode", choices=["security", "technical", "whitepaper"],
                        default="technical", help="Review-Modus (default: technical)")
    parser.add_argument("--context", default="", help="Pfad zu Kontext-Datei (vorherige Reviews etc.)")
    args = parser.parse_args()

    doc_path = Path(args.document)
    if not doc_path.exists():
        print(f"❌ Datei nicht gefunden: {doc_path}")
        sys.exit(1)

    label = args.label or doc_path.stem.replace("_", " ").replace("-", " ")

    context = ""
    if args.context:
        ctx_path = Path(args.context)
        if ctx_path.exists():
            context = ctx_path.read_text(encoding="utf-8")
            print(f"📎 Kontext geladen: {ctx_path.name} ({len(context)} Zeichen)")
        else:
            print(f"⚠️  Kontext-Datei nicht gefunden: {ctx_path}")

    asyncio.run(run_pipeline(doc_path, label, args.mode, context))


if __name__ == "__main__":
    main()
