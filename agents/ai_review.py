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

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from app import notify  # shared gate: app/notify.telegram_allowed

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
OPENAI_MAX_TOKENS = 16000      # gpt-5 ist ein Reasoning-Modell: Reasoning-Tokens zählen gegen max_completion_tokens.
                               # Bei 4000 fraß das Reasoning das ganze Budget → message.content war "" trotz 200 OK.
                               # 16000 + reasoning_effort="low" lässt genug Output-Budget für den eigentlichen Review.
OPENAI_REASONING_EFFORT = "low"  # Reasoning-Budget niedrig halten, damit Output-Tokens übrig bleiben
GEMINI_MAX_TOKENS = 16000      # Tier-Wechsel Flash→Pro (gemini-3.1-pro-preview); doppelter Headroom für vollständige Reviews
PERPLEXITY_MAX_TOKENS = 4000
MISTRAL_MAX_TOKENS = 4000      # nur im eu-compliance-Modus aktiv (4. Reviewer)
CLAUDE_MAX_TOKENS = 16000      # 4-Reviewer-Synthesen (eu-compliance, kya-peerreview, --kit) bei 4000
                               # abgeschnitten (stop_reason=max_tokens, Verdikt fehlte). Ceiling, kein
                               # Fixpreis — kleine Synthesen nutzen nur was sie brauchen.

# Synthese-Modell NICHT hartkodieren: env/secrets mit aktuellem Default. Ein retiretes Modell
# (z.B. claude-sonnet-4-20250514 → 404) muss laut scheitern, nicht still degradieren.
SYNTHESIS_MODEL = os.environ.get("SYNTHESIS_MODEL") or SECRETS.get("SYNTHESIS_MODEL") or "claude-sonnet-4-6"

# 4-Reviewer-Synthesen (bis 16k Output ueber ~46k Input) sprengen die alten 180s
# und scheitern als ReadTimeout mit leerer Fehlermeldung. Timeout grosszuegig, per
# env/secret uebersteuerbar. Fix: fix/ai-review-synthesis-timeout.
SYNTHESIS_TIMEOUT_S = int(os.environ.get("SYNTHESIS_TIMEOUT_S") or SECRETS.get("SYNTHESIS_TIMEOUT_S") or 600)

# Optionaler per-Reviewer-Prompt-Kit (JSON via --kit): generalisiert kya-peerreview auf beliebige
# Peer-Review-Runden, ohne paper-spezifische Prompts im Code zu hardcoden. Struktur:
#   {"shared_brief": str, "reviewers": {"openai"|"gemini"|"perplexity"|"mistral": str},
#    "synthesis_prompt": optional str mit {label}/{date}/{*_review}-Platzhaltern}
# Wird in main() aus --kit geladen; None = bisheriges Verhalten (Modus-Persona).
KIT = None


def load_kit(path: str) -> dict:
    """Lädt einen Prompt-Kit (JSON) und validiert die Pflichtfelder."""
    kit = json.loads(Path(path).read_text(encoding="utf-8"))
    if "shared_brief" not in kit or "reviewers" not in kit:
        raise ValueError("Kit braucht 'shared_brief' und 'reviewers'")
    return kit

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

# ── kya-peerreview: per-Reviewer-Prompts ──────────────────────────────────────
# Gemeinsamer Brief (TEIL 2 des Peer-Review-Kits) + reviewer-spezifischer Prompt (TEIL 3).
# Jeder Reviewer bekommt eine eigene Linse, statt einer geteilten Modus-Persona. Das interne
# Pre-Review (TEIL 1) ist BEWUSST NICHT enthalten — die Reviewer sollen unabhängig prüfen.
KYA_COMMON_BRIEF = """You are acting as an independent peer reviewer of the attached white paper,
"Know Your Agent (KYA) Whitepaper v4.0" by MolTrust / CryptoKRI GmbH.

This is a critical review, not an endorsement. Your job is to find the weak points,
not to praise the strong ones. Be specific, be adversarial where warranted, and
propose concrete rewrites rather than vague concerns.

Hold the paper to four standards throughout:

1. THE KYC ANALOGY IS THE ANCHOR. KYA is explicitly framed as the agent-economy
   successor to Know Your Customer. Test every claim against that analogy. Where KYA
   diverges from KYC's foundations (identifiability of the liable party, beneficial
   ownership, risk-based due diligence, auditable records), say so and judge whether
   the divergence is justified or whether it breaks the paper's own frame.

2. GLOBAL, NOT LOCAL. Assess requirements, solution approaches, and business realities
   across jurisdictions — at minimum EU, US, UK, Singapore/APAC, Switzerland, and the
   FATF-global baseline. Where a claim holds in one jurisdiction but fails in another,
   surface the conflict.

3. SEPARATE THE THREE LAYERS. For each major thesis, evaluate (a) the regulatory/legal
   requirement, (b) the technical solution approach and how it compares to alternatives,
   and (c) the business/economic reality of adoption. A thesis can be legally sound but
   economically naive, or technically elegant but competitively dominated.

4. STEELMAN THE OPPOSITION. For each load-bearing claim, state the strongest counter-
   argument an informed skeptic would make, then judge whether the paper survives it.

Deliver: (i) a ranked list of the most serious problems; (ii) for each, a concrete,
specific fix or rewrite; (iii) an overall verdict on whether the central thesis
("trust and liability are separate layers; trust must be recomputable") is defensible."""

KYA_REVIEWER_PROMPTS = {
    "perplexity": """Focus your review on the LIVE, SOURCED landscape. Search the web across jurisdictions
and return a sourced map of who agrees and who disagrees with this paper's positions.

For each of these theses, find the strongest published SUPPORTING view and the strongest
published OPPOSING view, each with source, author/institution, jurisdiction, and date:

- "Know Your Agent" as the successor to KYC — who is building it, and do they frame it
  the same way? (Look for: Skyfire, Trulioo, Visa, Mastercard "Verifiable Intent",
  Sumsub, Catena Labs, ERC-8004, Microsoft Entra Agent ID, AGNTCY/NANDA registries.)
- "Trust and liability are separate layers; an agent need not trace to a human per action."
  (Look for agent-liability law and scholarship: California's no-autonomy-defense law,
  Colorado AI Act, EU deployer liability, Dutch/Spanish DPA positions, Singapore MGF,
  FATF Recommendation 10 on anonymous accounts and beneficial ownership.)
- "Pseudonymous behavioral trust is sufficient because the DID is a dissolvable legal
  person." Does this survive FATF/AML scrutiny? Find the regulatory counterposition.
- "KYA adoption is pull-driven; regulators will arrive after the market sets the standard."
  Is the timeline evidence consistent with this, or is regulation arriving concurrently?

Then identify any competing standard or framework this paper FAILS TO MENTION but a
reader in 2026 would expect it to address. Rank the omissions by how damaging each is to
the paper's credibility. Cite everything; flag any claim in the paper you cannot
corroborate from independent sources.""",

    "openai": """Review for logical consistency and argumentative soundness. Ignore presentation; focus
on whether the arguments actually hold together.

Do the following:

1. Map the paper's core argument chain from premises to conclusions. Identify every point
   where a premise does not support its stated conclusion, where two claims are mutually
   inconsistent, or where a definition shifts between sections.

2. Stress-test the central thesis directly: "trust and liability are separate layers."
   State the strongest objection (the deploying party is liable regardless, so liability
   does not 'float free'), and judge whether the paper's answer (pseudonymous legal person
   as dissolvable backstop + trust score prices risk) actually defeats it. If it does not,
   rewrite the thesis so that it does — while keeping it useful to the paper's goals.

3. Audit the KYC analogy line by line. KYC requires an identifiable liable party, beneficial
   ownership, and risk-based due diligence. For each KYA claim that leans on the analogy,
   decide whether the analogy holds or is being stretched past the point where it supports
   the conclusion. Where it breaks, propose either a narrower claim or a different anchor.

4. Find every place the paper dismisses a counterargument too quickly (one sentence where
   a paragraph is owed) and supply the missing reasoning — or concede the point.

For every problem you identify, write the specific replacement sentence or paragraph.
Do not give general advice; give the edit.""",

    "gemini": """Review as a technical standards architect assessing whether the paper's differentiation
is technically real or merely rhetorical.

1. Position MolTrust against the live 2026 stack: W3C DID/VC, A2A, MCP, Microsoft Entra
   Agent ID, ERC-8004, AGNTCY/NANDA registries, eIDAS 2.0 / EUDI Wallet, x402, the
   Stripe/Tempo Machine Payments Protocol, Mastercard "Verifiable Intent", Skyfire. For
   each, state precisely where MolTrust overlaps and where it genuinely differs. Is
   "portable behavioral trust + recomputable governance" a defensible technical moat, or
   is it reconstructable on top of incumbents?

2. Stress-test the technical claims: the AAE schema (MANDATE/CONSTRAINTS/VALIDITY,
   default-deny, attenuation-only delegation, 8-hop cap); the three-layer enforcement
   (crypto / API / kernel-Falco) in advisory mode; the recomputable-trust / CEP design
   (five-condition predicate, honest-verifier data availability, keyed commitment +
   cryptographic erasure, cluster-diversity-from-graph). Identify overclaims, especially
   any that present designed-but-not-activated mechanisms as operational.

3. Evaluate the GDPR cryptographic-erasure argument technically: does destroying the key
   actually satisfy a right-to-erasure obligation when the ciphertext and the anchor
   persist? State the technical case for and against.

4. Judge the Sybil-resistance and cluster-diversity claims: can a patient, well-funded
   adversary defeat reciprocal-Jaccard clustering? Is "fabricated identities yield no
   fabricated consensus" technically warranted, or should it be softened?

Return concrete technical corrections, not impressions.""",

    "mistral": """Review strictly through an EU regulatory and digital-sovereignty lens. The author is a
Swiss-domiciled entity (CryptoKRI GmbH, Zürich) addressing EU relying parties, so test
both substantive EU law and cross-border/sovereignty exposure. Keep KYC / AMLD6 / FATF
as the anchor throughout.

Assess the paper against:

1. EU AI Act. Is the claim defensible that MolTrust is "not a deployer" and that oversight
   duty falls on the relying party (Art. 14, Art. 26)? Evaluate the stronger alternative
   argument that a deterministic verification layer is "not an AI system" under Art. 3(1)
   at all. Check the "supports Article 12 logging" wording. Is the risk-tiering of human
   oversight consistent with Art. 14's intent?

2. eIDAS 2.0 / EUDI Wallet. The paper does not mention it. Should MolTrust position as
   complementary to the EU's mandatory verifiable-credential identity layer (QEAAs from
   QTSPs)? How would an EU reviewer expect agent trust infrastructure to relate to eIDAS?

3. GDPR. Evaluate the on-chain immutability vs. right-to-erasure (Art. 17) tension and
   whether "cryptographic erasure" (destroying the key) is legally sufficient in the EU.
   Assess the AEPD "rule of 2" and the Dutch DPA's deployer-accountability stance as they
   bear on the paper's claims. Check the "no PII in standard configuration" framing.

4. AML / FATF / pseudonymity. Reconcile (or refute) the paper's pseudonymous-DID framing
   with FATF Recommendation 10's prohibition of anonymous accounts and beneficial-ownership
   requirement, and with AMLD6. Does Trust Tier 0 (KYC-verified) resolve this, and is it
   given enough weight?

5. Digital sovereignty. Given the Nov-2025 EU Declaration on Digital Sovereignty and the
   EU's "trustworthy AI" positioning, is a Swiss-neutral trust layer an asset or a liability
   for EU adoption? What adequacy/data-transfer points must the paper address?

For each issue, state the EU regulator's likely objection and the precise rewrite that
makes the paper defensible in the EU.""",
}


def resolve_system_prompt(mode: str, reviewer_key: str) -> str:
    """Per-Reviewer-Prompt-Auflösung, in dieser Priorität:
    1. --kit (KIT geladen): gemeinsamer Brief + reviewer-spezifischer Prompt aus dem Kit
       (generisch, paper-agnostisch — für beliebige Peer-Review-Runden).
    2. kya-peerreview: fest eingebauter v4.0-Brief + Linsen (Perplexity=Web/Jurisdiktionen,
       GPT-5=Logik, Gemini=Standards/Technik, Mistral=EU-Recht/Souveränität).
    3. Standard-Modi: eine Persona pro Modus (gleich für alle Reviewer)."""
    if KIT is not None:
        rp = KIT["reviewers"].get(reviewer_key, "")
        return KIT["shared_brief"] + (("\n\n---\n\n" + rp) if rp else "")
    if mode == "kya-peerreview":
        return KYA_COMMON_BRIEF + "\n\n---\n\n" + KYA_REVIEWER_PROMPTS[reviewer_key]
    return REVIEW_PROMPTS[mode]


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

SYNTHESIS_PROMPT_KYA = """Du bist Lead-Reviewer bei MolTrust. Vier unabhängige AI-Reviewer haben das KYA-Whitepaper v4.0 aus je eigener Linse KRITISCH begutachtet (kein Endorsement — Auftrag war, die Schwachstellen zu finden):
- GPT-5 (OpenAI) — Logik & Argumentationskonsistenz
- Gemini 3.1 Pro Preview (Google) — Standards- & Technik-Landschaft
- Perplexity Sonar Pro — Web/Jurisdiktionen, gesourcte Gegenpositionen
- Mistral Large (EU/Frankreich) — EU-Recht & digitale Souveränität

Gemeinsamer Anker: die KYC-Analogie, auf die das Paper baut (identifizierbare haftbare Person, Beneficial Ownership, Risk-Based Due Diligence, auditierbare Records). Synthetisiere alle vier Reviews in ein klares Entscheidungsdokument für den Autor.

Strukturiere exakt so:

# KYA v4.0 Peer-Review Synthesis — {label}
**Datum:** {date}
**Reviewer:** GPT-5 + Gemini 3.1 Pro Preview + Perplexity Sonar Pro + Mistral Large → Synthese via Claude

---

## 🔴 Ranked: Schwerwiegendste Probleme (Konsens)
(Nach Tragweite geordnet. Punkte, die ≥2 Reviewer als ernstes Problem sehen. Pro Punkt: These im Paper → Schwachstelle → welche Reviewer sie nennen.)

## 🟡 Divergenz: Wo die Reviewer sich widersprechen
(Mit kurzer Bewertung, wer Recht hat.)

## 🔵 Perplexity Fact-Check (Web/Jurisdiktionen)
(Welche Claims sind extern nicht belegbar? Welche Wettbewerber/Standards/Gegenpositionen fehlen im Paper, und wie schädlich ist jede Lücke?)

## 🇪🇺 Mistral EU-Nuance
(EU-rechtliche/Souveränitäts-Punkte, die die anderen übersehen — AI Act Art. 3(1)/14/26, eIDAS 2.0/EUDI, GDPR Art. 17, FATF R.10/AMLD6.)

## 🛠️ Konkrete Fixes (priorisiert)
(Pro Top-Problem ein konkreter, spezifischer Rewrite/Edit — kein vager Rat. Max. 12 Items.)

## ✅ Verdikt zur Kernthese
Ist die zentrale These — „Trust und Haftung sind getrennte Schichten; Trust muss recomputable sein" — verteidigbar? Klares Votum: VERTEIDIGBAR / VERTEIDIGBAR MIT NACHBESSERUNG / NICHT HALTBAR — mit 3-Satz-Begründung.

---

GPT-5 Review (Logik & Argumentation):
{openai_review}

Gemini Review (Standards & Technik):
{gemini_review}

Perplexity Review (Web & Jurisdiktionen):
{perplexity_review}

Mistral Review (EU-Recht & Souveränität):
{mistral_review}
"""

# ── API Calls ────────────────────────────────────────────────────────────────

def _finalize(model: str, content: str, tokens) -> dict:
    """GUARD 1 (silent-empty): Ein konfigurierter Reviewer, der leeren/whitespace-only
    Content liefert (z.B. Reasoning-Modell, dessen Output-Budget vom Reasoning aufgebraucht
    wurde — 200 OK, aber message.content == ""), wird NICHT als Erfolg gewertet. Stattdessen
    error=True + empty=True, damit run_pipeline WARN loggt und der Output-Header es ausweist."""
    if not content or not str(content).strip():
        return {"model": model, "content": "(kein Inhalt — Reviewer lieferte leere Antwort trotz API-200)",
                "tokens": tokens, "error": True, "empty": True}
    return {"model": model, "content": content, "tokens": tokens, "error": False}


async def call_openai(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """GPT-5 Review Call"""
    if not OPENAI_KEY:
        return {"model": "GPT-5", "content": "ERROR: OPENAI_API_KEY nicht gesetzt", "error": True}

    system_prompt = resolve_system_prompt(mode, "openai")
    payload = {
        "model": "gpt-5",
        "max_completion_tokens": OPENAI_MAX_TOKENS,
        "reasoning_effort": OPENAI_REASONING_EFFORT,
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
        return _finalize("GPT-5", content, tokens)
    except Exception as e:
        return {"model": "GPT-5", "content": f"ERROR: {e}", "error": True}


async def call_gemini(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """Gemini 3.1 Pro Preview Review Call — with retry on 503"""
    if not GEMINI_KEY:
        return {"model": "Gemini 3.1 Pro Preview", "content": "ERROR: GEMINI_API_KEY nicht gesetzt", "error": True}

    system_prompt = resolve_system_prompt(mode, "gemini")
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
            return _finalize("Gemini 3.1 Pro Preview", content, tokens)
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

    system_prompt = resolve_system_prompt(mode, "perplexity") + PERPLEXITY_EXTRA.get(mode, "")
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
        return _finalize("Perplexity Sonar Pro", content, tokens)
    except Exception as e:
        return {"model": "Perplexity Sonar Pro", "content": f"ERROR: {e}", "error": True}


async def call_mistral(client: httpx.AsyncClient, document: str, mode: str) -> dict:
    """Mistral Large Review Call — EU-regulatory perspective (api.mistral.ai, OpenAI-kompatibel).
    Nur in den 4-Reviewer-Modi (eu-compliance, kya-peerreview) aktiv (siehe REVIEWERS_BY_MODE)."""
    if not MISTRAL_KEY:
        return {"model": "Mistral Large", "content": "ERROR: MISTRAL_API_KEY nicht gesetzt", "error": True}

    system_prompt = resolve_system_prompt(mode, "mistral")
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
        return _finalize("Mistral Large", content, tokens)
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

    if KIT is not None and KIT.get("synthesis_prompt"):
        user_prompt = KIT["synthesis_prompt"].format(
            label=label,
            date=date_str,
            openai_review=by_model.get("GPT-5", "(kein Review)"),
            gemini_review=by_model.get("Gemini 3.1 Pro Preview", "(kein Review)"),
            perplexity_review=by_model.get("Perplexity Sonar Pro", "(kein Review)"),
            mistral_review=by_model.get("Mistral Large", "(kein Review)"),
        )
    elif mode == "kya-peerreview":
        user_prompt = SYNTHESIS_PROMPT_KYA.format(
            label=label,
            date=date_str,
            openai_review=by_model.get("GPT-5", "(kein Review)"),
            gemini_review=by_model.get("Gemini 3.1 Pro Preview", "(kein Review)"),
            perplexity_review=by_model.get("Perplexity Sonar Pro", "(kein Review)"),
            mistral_review=by_model.get("Mistral Large", "(kein Review)"),
        )
    elif mode == "eu-compliance":
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
        "model": SYNTHESIS_MODEL,
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
            timeout=SYNTHESIS_TIMEOUT_S
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]
    except httpx.HTTPStatusError as e:
        # GUARD 2: laut scheitern statt still degradieren. 404 = Modell (vermutlich) retired.
        code = e.response.status_code
        body = (e.response.text or "")[:300]
        if code == 404:
            return (f"ERROR Synthesis: HTTP 404 — Synthese-Modell '{SYNTHESIS_MODEL}' nicht gefunden "
                    f"(vermutlich retired). SYNTHESIS_MODEL env/secret auf ein aktuelles Modell setzen. "
                    f"API-Body: {body}")
        return f"ERROR Synthesis: HTTP {code} bei Modell '{SYNTHESIS_MODEL}' — {body}"
    except Exception as e:
        return f"ERROR Synthesis: {e}"


async def send_telegram(client: httpx.AsyncClient, message: str):
    """Telegram Notification"""
    if not notify.telegram_allowed("ai_review.send_telegram"):
        return
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
    "kya-peerreview": [call_openai, call_gemini, call_perplexity, call_mistral],
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
            if r.get("empty"):
                print(f"   {r['model']:<22}: ⚠️  FAILED — kein Inhalt (leere Antwort trotz API-200), als Fehler gewertet")
            elif r["error"]:
                print(f"   {r['model']:<22}: ❌ ({r.get('tokens', '?')} Tokens)")
            else:
                print(f"   {r['model']:<22}: ✅ ({r.get('tokens', '?')} Tokens)")

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

        def _status(r):
            if r.get("empty"):
                return "⚠️ no content (FAILED — leere Antwort trotz API-200)"
            if r["error"]:
                return "❌ error"
            return "✅ ok"
        reviewer_status_lines = "\n".join(f"- {r['model']}: {_status(r)}" for r in results)
        any_reviewer_failed = any(r["error"] for r in results)

        raw_blocks = "\n\n".join(
            f"<details>\n<summary>{r['model']} Raw Review</summary>\n\n{r['content']}\n\n</details>"
            for r in results
        )

        full_output = f"""# AI Review: {label}
**Generiert:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")}
**Quelle:** {doc_path.name}
**Modus:** {mode}
**Reviewer:** {reviewer_line} → Claude Synthesis ({SYNTHESIS_MODEL})

**Reviewer-Status:**
{reviewer_status_lines}
{"" if not any_reviewer_failed else chr(10) + "> ⚠️ Mindestens ein Reviewer hat KEINEN Inhalt geliefert — Synthese basiert auf einem reduzierten Panel."}

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
    parser.add_argument("--mode", choices=["security", "technical", "whitepaper", "product", "eu-compliance", "kya-peerreview"],
                        default="technical", help="Review-Modus (default: technical). product = Pricing-/Product-IA-Review (3 Reviewer). eu-compliance + kya-peerreview fügen Mistral als 4. Reviewer hinzu. kya-peerreview = per-Reviewer-Linsen (TEIL 2 Brief + TEIL 3 Prompts).")
    parser.add_argument("--context", default="", help="Pfad zu Kontext-Datei (vorherige Reviews etc.)")
    parser.add_argument("--kit", default="", help="Pfad zu per-Reviewer-Prompt-Kit (JSON): shared_brief + reviewers{openai,gemini,perplexity,mistral} (+ optional synthesis_prompt). Generisch für Peer-Review-Runden; überschreibt die Modus-Persona. Mit --mode kya-peerreview für 4 Reviewer.")
    args = parser.parse_args()

    doc_path = Path(args.document)
    if not doc_path.exists():
        print(f"❌ Datei nicht gefunden: {doc_path}")
        sys.exit(1)

    if args.kit:
        global KIT
        KIT = load_kit(args.kit)
        print(f"🧩 Prompt-Kit geladen: {Path(args.kit).name} ({KIT.get('name','?')}) — Reviewer: {', '.join(KIT['reviewers'])}")

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
