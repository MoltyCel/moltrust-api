#!/usr/bin/env python3
"""
MolTrust Multi-AI Review Pipeline v2
Sendet MD-Dokumente an OpenAI + Gemini + Perplexity, synthetisiert via Claude, Telegram-Alert.

Im Modus --mode eu-compliance kommt zusätzlich Mistral Large als 4. Reviewer dazu
(EU-regulatorische Nuance: AI Act, DSGVO, eIDAS 2.0, NIS2). Die drei Standard-Modi
(security|technical|whitepaper) bleiben unverändert bei 3 Reviewern.

Usage:
  python3 ai_review.py <path/to/document.md> [--label "Security Konzept v1"] [--mode security|technical|whitepaper|eu-compliance]
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
                "PERPLEXITY_API_KEY", "MISTRAL_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        if os.environ.get(key):
            secrets[key] = os.environ[key]
    return secrets

SECRETS = load_secrets()

OPENAI_KEY      = SECRETS.get("OPENAI_API_KEY", "")
GEMINI_KEY      = SECRETS.get("GEMINI_API_KEY", "")
ANTHROPIC_KEY   = SECRETS.get("ANTHROPIC_API_KEY", "")
PERPLEXITY_KEY  = SECRETS.get("PERPLEXITY_API_KEY", "")
MISTRAL_KEY     = SECRETS.get("MISTRAL_API_KEY", "")
TG_TOKEN        = SECRETS.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID      = SECRETS.get("TELEGRAM_CHAT_ID", "")

OUTPUT_DIR = Path.home() / "moltstack" / "reviews"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Limits ───────────────────────────────────────────────────────────────────
INPUT_CHAR_LIMIT = 60000       # gpt-5 + gemini-3.1-pro-preview: beide Pro-Tier mit großem Kontextfenster — 60k chars is safe
OPENAI_MAX_TOKENS = 4000       # v1 was 2000 — truncated long reviews
GEMINI_MAX_TOKENS = 16000      # Tier-Wechsel Flash→Pro (gemini-3.1-pro-preview); doppelter Headroom für vollständige Reviews
PERPLEXITY_MAX_TOKENS = 4000
MISTRAL_MAX_TOKENS = 4000      # nur im eu-compliance-Modus aktiv (4. Reviewer)
CLAUDE_MAX_TOKENS = 4000       # v1 was 3000 — more room for 3-reviewer synthesis

# ── Review-Prompts je Modus ──────────────────────────────────────────────────
EU_COMPLIANCE_PROMPT = """Du bist ein unabhängiger EU-Regulatory-Compliance-Reviewer für dezentrale KI- und Identitäts-Infrastruktur.
Analysiere das folgende Dokument ausschließlich aus Sicht der EU-Regulierung — mit Blick auf MolTrust als in EU/Schweiz ansässigen Anbieter (CryptoKRI GmbH, Zürich).

Berücksichtige insbesondere die folgenden Rechtsakte:
- EU AI Act: Risikoklassen, GPAI-Pflichten, Transparenz- und Hochrisiko-Anforderungen, Fristen
- DSGVO / GDPR: Rechtsgrundlagen, Datenminimierung, Betroffenenrechte, Drittland-Transfers
- eIDAS 2.0: EUDI Wallet, Qualified Trust Services, Verifiable Credentials, Levels of Assurance
- NIS2: Cybersicherheits- und Meldepflichten, Lieferketten-/Supply-Chain-Sicherheit

Strukturiere deine Antwort exakt so:
## 1. Regulatorische Einordnung (welche Rechtsakte greifen, in welcher Rolle?)
## 2. Kritische Compliance-Lücken
## 3. Mittlere Compliance-Risiken
## 4. Konkrete Maßnahmen (priorisiert, mit Bezug auf Artikel/Erwägungsgründe)
## 5. EU-Marktpositionierung & Anchors (Western/EU-Vorteile, Standard-Konformität)

Sei präzise und juristisch-technisch fundiert, keine Marketing-Sprache. Beziehe dich wo möglich auf konkrete Artikel."""

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

Konstruktiv aber direkt.""",

    "product": """Du bist ein erfahrener Product-/Pricing-Reviewer für Developer-Tools und API-Infrastruktur.
Du kennst die Pricing- und Informationsarchitektur-Muster von Stripe, Vercel, Twilio, Linear,
Clerk/Auth0, Cloudflare. Du bewertest AUSSCHLIESSLICH: Struktur, Informationsarchitektur,
Free-Tier-/Credit-Modell-Darstellung, Wording/Verständlichkeit und Developer-Onboarding-Friktion.
NICHT zu bewerten: die nominale Preishöhe (gesetzt), keine Standards-/Citation-Prüfung, keine
Protokoll-/W3C-Aspekte. Leitfrage: Wie präsentiert man dieses Pricing so, dass Adoption im
Developer-/A2A-Umfeld maximal reibungsarm ist? Liefere konkrete, umsetzbare Empfehlungen und
benenne Anti-Patterns aus vergleichbaren Dev-Tool-Pricings.

Strukturiere deine Antwort exakt so:
## 1. Eine Seite vs. mehrere — Empfehlung + Begründung (Developer-first)
## 2. Informationsarchitektur / Sektionierung (inkl. Einstiegsseite, Slug-/Link-Achse falls Split)
## 3. Free-Layer-Präsentation (Hook, Wording, Reihenfolge)
## 4. Credit-Klammer — verbinden oder entkoppeln, und wie darstellen
## 5. Subscription-Card-Komposition (was prominent, „Early Access"-Wording)
## 6. Maschinenlesbarkeit (menschliche /pricing vs. /billing/plans · x402.json — gleiche Struktur?)
## 7. Anti-Patterns aus vergleichbaren Dev-Tool-/Infra-Pricings

Konkret und umsetzbar, keine Marketing-Sprache.""",

    "eu-compliance": EU_COMPLIANCE_PROMPT
}

PERPLEXITY_EXTRA = {
    "security": "\n\nZusätzlich: Recherchiere aktuelle CVEs und bekannte Angriffsvektoren die für dieses System relevant sind. Prüfe ob die referenzierten Standards und Frameworks aktuell und korrekt zitiert sind.",
    "technical": "\n\nZusätzlich: Prüfe ob die referenzierten Standards (W3C, IETF, DIF) korrekt und aktuell zitiert sind. Recherchiere ob es neuere Versionen oder relevante Ergänzungen gibt.",
    "whitepaper": "\n\nZusätzlich: Prüfe ob alle zitierten Quellen existieren, korrekt zitiert sind, und ob es wichtige aktuelle Arbeiten gibt die fehlen. Recherchiere den aktuellen Stand der referenzierten Projekte und Frameworks.",
    "eu-compliance": "\n\nZusätzlich: Recherchiere den aktuellen Stand von EU AI Act, DSGVO-Leitlinien (EDPB), eIDAS 2.0 / EUDI-Wallet-Spezifikationen und NIS2-Umsetzung. Prüfe ob zitierte Rechtsakte, Artikel und Fristen korrekt und aktuell sind, und ob relevante Delegated/Implementing Acts oder Guidelines fehlen.",
    "product": "\n\nZusätzlich: Recherchiere, wie vergleichbare Developer-Tools / API-Infrastrukturen (z.B. Stripe, Vercel, Twilio, Cloudflare, Clerk/Auth0, OpenAI/Anthropic-API, usage-based/credit-Modelle) ihr Pricing strukturieren und präsentieren — Free-Tier-Darstellung, Credit-/Usage-Mechanik, eine Seite vs. mehrere, Self-Serve vs. Contact-Sales-Trennung. Leite konkrete, übertragbare Muster und Anti-Patterns ab. KEINE Standards-/Citation-Prüfung."
}

SYNTHESIS_PROMPT = """Du bist Lead-Reviewer bei MolTrust. Du hast Reviews von drei unabhängigen AI-Modellen zu demselben Dokument erhalten:
- GPT-5 (OpenAI) — technische Analyse
- Gemini 3.1 Pro Preview (Google) — technische Analyse
- Perplexity Sonar Pro — Analyse mit Echtzeit-Web-Recherche (Referenz-Checks, Aktualität)

Deine Aufgabe: Synthetisiere alle drei Reviews in ein klares Entscheidungsdokument für den Gründer.

Strukturiere exakt so:

# Synthesis Review — {label}
**Datum:** {date}
**Reviewer:** GPT-5 + Gemini 3.1 Pro Preview + Perplexity Sonar Pro → Synthese via Claude

---

## 🔴 Konsens: Kritische Punkte
(Punkte, die mindestens ZWEI von drei Reviews als Problem sehen)

## 🟡 Divergenz: Unterschiedliche Einschätzungen
(Wo die Reviewer sich widersprechen — mit kurzer Bewertung wer Recht hat)

## 🔵 Perplexity Fact-Check
(Was hat Perplexity's Web-Recherche ergeben? Falsche Referenzen? Fehlende aktuelle Quellen? Veraltete Standards?)

## 🟢 Konsens: Stärken
(Punkte, die mindestens ZWEI von drei positiv bewerten)

## 📋 Priorisierte Aktionsliste
(Konkrete TODOs, nach Dringlichkeit sortiert — max. 10 Items)

## ✅ Freigabe-Empfehlung
Klares Votum: FREIGEBEN / ÜBERARBEITEN / GRUNDLEGEND ÜBERDENKEN — mit 2-Satz-Begründung.

---

GPT-5 Review:
{openai_review}

Gemini Review:
{gemini_review}

Perplexity Review:
{perplexity_review}
"""

SYNTHESIS_PROMPT_EU = """Du bist Lead-Reviewer bei MolTrust. Du hast EU-Compliance-Reviews von vier unabhängigen AI-Modellen zu demselben Dokument erhalten:
- GPT-5 (OpenAI) — regulatorische Analyse
- Gemini 3.1 Pro Preview (Google) — regulatorische Analyse
- Perplexity Sonar Pro — Analyse mit Echtzeit-Web-Recherche (Rechtsakt-Aktualität, Fristen)
- Mistral Large (Mistral AI, EU/Frankreich) — EU-regulatorische Nuance (AI Act, DSGVO, eIDAS 2.0, NIS2)

Deine Aufgabe: Synthetisiere alle vier Reviews in ein klares EU-Compliance-Entscheidungsdokument für den Gründer.

Strukturiere exakt so:

# EU-Compliance Synthesis Review — {label}
**Datum:** {date}
**Reviewer:** GPT-5 + Gemini 3.1 Pro Preview + Perplexity Sonar Pro + Mistral Large → Synthese via Claude

---

## 🔴 Konsens: Kritische Compliance-Punkte
(Punkte, die mindestens ZWEI von vier Reviews als Problem sehen)

## 🟡 Divergenz: Unterschiedliche Einschätzungen
(Wo die Reviewer sich widersprechen — mit kurzer Bewertung wer Recht hat)

## 🔵 Perplexity Fact-Check
(Was hat Perplexity's Web-Recherche zur Aktualität von Rechtsakten/Fristen ergeben?)

## 🇪🇺 Mistral EU-Nuance
(Welche EU-spezifische regulatorische Nuance bringt Mistral ein, die die anderen übersehen?)

## 🟢 Konsens: bereits konforme Stärken
(Punkte, die mindestens ZWEI von vier positiv bewerten)

## 📋 Priorisierte Compliance-Aktionsliste
(Konkrete TODOs mit Bezug auf Artikel/Rechtsakt, nach Dringlichkeit — max. 10 Items)

## ✅ Compliance-Freigabe-Empfehlung
Klares Votum: KONFORM / NACHBESSERN / GRUNDLEGEND ÜBERARBEITEN — mit 2-Satz-Begründung.

---

GPT-5 Review:
{openai_review}

Gemini Review:
{gemini_review}

Perplexity Review:
{perplexity_review}

Mistral Review:
{mistral_review}
"""

# ── API Calls ────────────────────────────────────────────────────────────────

async def call_openai(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """GPT-5 Review Call"""
    if not OPENAI_KEY:
        return {"model": "GPT-5", "content": "ERROR: OPENAI_API_KEY nicht gesetzt", "error": True}

    system_prompt = REVIEW_PROMPTS[mode]
    payload = {
        "model": "gpt-5",
        "max_completion_tokens": OPENAI_MAX_TOKENS,
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
            timeout=180
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", "?")
        return {"model": "GPT-5", "content": content, "tokens": tokens, "error": False}
    except Exception as e:
        return {"model": "GPT-5", "content": f"ERROR: {e}", "error": True}


async def call_gemini(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """Gemini 3.1 Pro Preview Review Call — with retry on 503"""
    if not GEMINI_KEY:
        return {"model": "Gemini 3.1 Pro Preview", "content": "ERROR: GEMINI_API_KEY nicht gesetzt", "error": True}

    system_prompt = REVIEW_PROMPTS[mode]
    combined_prompt = f"{system_prompt}\n\nHier ist das Dokument zur Review:\n\n{document}"

    payload = {
        "contents": [{"parts": [{"text": combined_prompt}]}],
        "generationConfig": {"maxOutputTokens": GEMINI_MAX_TOKENS, "temperature": 0.3}
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent"

    last_error = None
    for attempt in range(3):
        try:
            resp = await client.post(url, json=payload, headers={"x-goog-api-key": GEMINI_KEY}, timeout=300)
            if resp.status_code == 503 and attempt < 2:
                wait = 10 * (attempt + 1)
                print(f"   Gemini 503 — retry {attempt+1}/2 in {wait}s...")
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            tokens = data.get("usageMetadata", {}).get("totalTokenCount", "?")
            return {"model": "Gemini 3.1 Pro Preview", "content": content, "tokens": tokens, "error": False}
        except Exception as e:
            last_error = e
            if attempt < 2:
                wait = 10 * (attempt + 1)
                print(f"   Gemini error — retry {attempt+1}/2 in {wait}s...")
                await asyncio.sleep(wait)
    return {"model": "Gemini 3.1 Pro Preview", "content": f"ERROR after 3 attempts: {last_error}", "error": True}


async def call_perplexity(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """Perplexity Sonar Pro Review Call — with web search"""
    if not PERPLEXITY_KEY:
        return {"model": "Perplexity Sonar Pro", "content": "ERROR: PERPLEXITY_API_KEY nicht gesetzt", "error": True}

    system_prompt = REVIEW_PROMPTS[mode] + PERPLEXITY_EXTRA[mode]
    payload = {
        "model": "sonar-pro",
        "max_tokens": PERPLEXITY_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Hier ist das Dokument zur Review:\n\n{document}"}
        ]
    }

    try:
        resp = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PERPLEXITY_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=180
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", "?")
        return {"model": "Perplexity Sonar Pro", "content": content, "tokens": tokens, "error": False}
    except Exception as e:
        return {"model": "Perplexity Sonar Pro", "content": f"ERROR: {e}", "error": True}


async def call_mistral(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """Mistral Large Review Call — EU-regulatory perspective (api.mistral.ai, OpenAI-kompatibel).
    Nur im eu-compliance-Modus aktiv (siehe REVIEWERS_BY_MODE)."""
    if not MISTRAL_KEY:
        return {"model": "Mistral Large", "content": "ERROR: MISTRAL_API_KEY nicht gesetzt", "error": True}

    system_prompt = REVIEW_PROMPTS[mode]
    payload = {
        "model": "mistral-large-latest",
        "max_tokens": MISTRAL_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Hier ist das Dokument zur Review:\n\n{document}"}
        ]
    }

    try:
        resp = await client.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=180
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", "?")
        return {"model": "Mistral Large", "content": content, "tokens": tokens, "error": False}
    except Exception as e:
        return {"model": "Mistral Large", "content": f"ERROR: {e}", "error": True}


async def call_claude_synthesis(client: httpx.AsyncClient, results: list, label: str, mode: str) -> str:
    """Claude synthetisiert alle Reviews. 3 Reviewer in den Standard-Modi,
    4 (inkl. Mistral) im eu-compliance-Modus. Template wird modus-abhängig gewählt,
    damit die Standard-Modi byte-gleich zur 3-Reviewer-Synthese bleiben."""
    if not ANTHROPIC_KEY:
        return "ERROR: ANTHROPIC_API_KEY nicht gesetzt"

    by_model = {r["model"]: r["content"] for r in results}
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    if mode == "eu-compliance":
        user_prompt = SYNTHESIS_PROMPT_EU.format(
            label=label,
            date=date_str,
            openai_review=by_model.get("GPT-5", "(kein Review)"),
            gemini_review=by_model.get("Gemini 3.1 Pro Preview", "(kein Review)"),
            perplexity_review=by_model.get("Perplexity Sonar Pro", "(kein Review)"),
            mistral_review=by_model.get("Mistral Large", "(kein Review)"),
        )
    else:
        user_prompt = SYNTHESIS_PROMPT.format(
            label=label,
            date=date_str,
            openai_review=by_model.get("GPT-5", "(kein Review)"),
            gemini_review=by_model.get("Gemini 3.1 Pro Preview", "(kein Review)"),
            perplexity_review=by_model.get("Perplexity Sonar Pro", "(kein Review)"),
        )

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": CLAUDE_MAX_TOKENS,
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
            timeout=180
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


# ── Reviewer-Set je Modus ─────────────────────────────────────────────────────
# Standard-Modi: 3 Reviewer (unverändert). eu-compliance: + Mistral als 4. Reviewer.
REVIEWERS_BY_MODE = {
    "security":      [call_openai, call_gemini, call_perplexity],
    "technical":     [call_openai, call_gemini, call_perplexity],
    "whitepaper":    [call_openai, call_gemini, call_perplexity],
    "product":       [call_openai, call_gemini, call_perplexity],
    "eu-compliance": [call_openai, call_gemini, call_perplexity, call_mistral],
}

# Anzeigenamen für das Banner (vor dem Call bekannt; entsprechen den result["model"]-Werten)
REVIEWER_LABELS = {
    call_openai:     "GPT-5",
    call_gemini:     "Gemini 3.1 Pro Preview",
    call_perplexity: "Perplexity Sonar Pro",
    call_mistral:    "Mistral Large",
}


# ── Main Pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(doc_path: Path, label: str, mode: str, context: str = ""):
    document = doc_path.read_text(encoding="utf-8")
    if context:
        document = f"## Kontext aus vorherigen Reviews\n\n{context}\n\n---\n\n## Dokument zur Review\n\n{document}"
    word_count = len(document.split())
    char_count = len(document)

    reviewer_fns = REVIEWERS_BY_MODE[mode]
    reviewer_label_str = " + ".join(REVIEWER_LABELS[fn] for fn in reviewer_fns)

    print(f"\n{'='*60}")
    print(f"🚀 MolTrust AI Review Pipeline v2")
    print(f"   Dokument : {doc_path.name} ({word_count} Wörter)")
    print(f"   Label    : {label}")
    print(f"   Modus    : {mode}")
    print(f"   Reviewer : {reviewer_label_str}")
    print(f"{'='*60}\n")

    if char_count > INPUT_CHAR_LIMIT:
        document = document[:INPUT_CHAR_LIMIT] + "\n\n[... Dokument gekürzt für Review ...]"
        print(f"⚠️  Dokument auf {INPUT_CHAR_LIMIT:,} Zeichen gekürzt (war {char_count:,})\n")

    async with httpx.AsyncClient() as client:
        # 1. Parallel Reviews — Reviewer-Set ist modus-abhängig (siehe REVIEWERS_BY_MODE)
        print(f"📤 Sende an {reviewer_label_str} (parallel)...")
        tasks = [fn(client, document, mode) for fn in reviewer_fns]
        results = await asyncio.gather(*tasks)

        for r in results:
            print(f"   {r['model']:<22}: {'✅' if not r['error'] else '❌'} ({r.get('tokens', '?')} Tokens)")

        # 2. Synthesis via Claude
        print("\n🧠 Synthetisiere via Claude...")
        synthesis = await call_claude_synthesis(client, results, label, mode)
        synthesis_failed = (
            not isinstance(synthesis, str)
            or not synthesis.strip()
            or synthesis.lstrip().startswith(("ERROR Synthesis", "ERROR:"))
        )
        print(f"   Synthesis : {'❌ FEHLGESCHLAGEN' if synthesis_failed else '✅'}")

        # 3. Output-File schreiben
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = label.replace(" ", "_").replace("/", "-")[:40]
        output_path = OUTPUT_DIR / f"{ts}_{safe_label}_review.md"

        reviewer_line = " + ".join(r["model"] for r in results)
        raw_blocks = "\n\n".join(
            f"<details>\n<summary>{r['model']} Raw Review</summary>\n\n{r['content']}\n\n</details>"
            for r in results
        )

        full_output = f"""# AI Review: {label}
**Generiert:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")}
**Quelle:** {doc_path.name}
**Modus:** {mode}
**Reviewer:** {reviewer_line} → Claude Synthesis

---

{synthesis}

---

## Raw Reviews

{raw_blocks}
"""
        output_path.write_text(full_output, encoding="utf-8")
        print(f"\n💾 Gespeichert: {output_path}")

        # 4. Telegram Alert
        n = len(results)
        errors = [r["model"] for r in results if r["error"]]
        status = f"✅ Vollständig ({n}/{n})" if not errors else f"⚠️ Fehler bei: {', '.join(errors)}"

        tg_msg = (
            f"🔍 *AI Review v2 abgeschlossen*\n"
            f"Label: `{label}`\n"
            f"Modus: `{mode}`\n"
            f"Status: {status}\n"
            f"File: `{output_path.name}`\n\n"
            f"Reviewer: {reviewer_label_str} → Claude"
        )
        await send_telegram(client, tg_msg)
        print("📱 Telegram Alert gesendet\n")

    print(f"{'='*60}")
    if synthesis_failed:
        print("❌ Review FEHLGESCHLAGEN: Synthese-Schritt nicht erfolgreich.")
        print(f"   Raw-Reviews + Fehlertext gespeichert: {output_path.name}")
        print(f"{'='*60}\n")
        sys.exit(2)
    print(f"✅ Review abgeschlossen: {output_path.name}")
    print(f"{'='*60}\n")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="MolTrust Multi-AI Review Pipeline v2")
    parser.add_argument("document", help="Pfad zum MD-Dokument")
    parser.add_argument("--label", default="", help="Bezeichnung für den Review")
    parser.add_argument("--mode", choices=["security", "technical", "whitepaper", "product", "eu-compliance"],
                        default="technical", help="Review-Modus (default: technical). product = Pricing-/Product-IA-Review (3 Reviewer). eu-compliance fügt Mistral als 4. Reviewer hinzu.")
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
