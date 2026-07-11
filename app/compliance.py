"""EU AI Act (Reg (EU) 2024/1689) compliance engine.

Deterministic, rule-based classification + Annex-V declaration builder + HTML
report renderer for the /compliance/* endpoints. Every normative statement is
pinned to the verified spec-fakten in docs/spec-fakten/eu-ai-act-2024-1689.md
(CELEX 32024R1689, retrieved 2026-07-11).

Design note (protocol layer, not legal advice): classification is a
deterministic function of explicit structured signals, with free-text keywords
used only as a hint. The output is a protocol-layer assessment that carries its
legal pins so a human/legal reviewer can audit it — it is NOT itself a legal
determination. This is the "deterministic protocol layer vs. model
self-report" property the /compliance/* surface is built to provide.
"""
from __future__ import annotations

import html
import datetime

CELEX = "Reg (EU) 2024/1689"

# --- Risk tiers (Art 5 / Art 6 / Art 50 / Art 95) ---------------------------
TIER_PROHIBITED = "prohibited"
TIER_HIGH = "high"
TIER_LIMITED = "limited"
TIER_MINIMAL = "minimal"
TIERS = (TIER_PROHIBITED, TIER_HIGH, TIER_LIMITED, TIER_MINIMAL)

# --- Art 5(1) prohibited practices: self-declared flag -> (label, pin) ------
ART5_PROHIBITED = {
    "manipulation": ("Subliminal/manipulative/deceptive techniques causing significant harm", f"{CELEX}, Art 5(1)(a)"),
    "vulnerability_exploitation": ("Exploiting vulnerabilities (age/disability/social-economic)", f"{CELEX}, Art 5(1)(b)"),
    "social_scoring": ("Social scoring leading to detrimental treatment", f"{CELEX}, Art 5(1)(c)"),
    "predictive_policing": ("Individual predictive policing based solely on profiling", f"{CELEX}, Art 5(1)(d)"),
    "facial_scraping": ("Untargeted scraping of facial images", f"{CELEX}, Art 5(1)(e)"),
    "emotion_workplace_education": ("Emotion inference in workplace/education", f"{CELEX}, Art 5(1)(f)"),
    "sensitive_biometric_categorisation": ("Biometric categorisation of sensitive attributes", f"{CELEX}, Art 5(1)(g)"),
    "realtime_rbi_law_enforcement": ("Real-time remote biometric ID in public for law enforcement", f"{CELEX}, Art 5(1)(h)"),
}
# Keyword hints (free-text) -> prohibited flag. Hints only; a hit is surfaced as
# "review_recommended", never a hard PROHIBITED verdict without a structured flag.
ART5_KEYWORD_HINTS = {
    "social scoring": "social_scoring",
    "social score": "social_scoring",
    "predict": "predictive_policing",
    "scrape facial": "facial_scraping",
    "scraping facial": "facial_scraping",
    "emotion recognition at work": "emotion_workplace_education",
    "subliminal": "manipulation",
}

# --- Annex III high-risk areas: number -> (label, pin, keyword hints) --------
ANNEX_III_AREAS = {
    1: ("Biometrics (remote ID, categorisation, emotion recognition)", f"{CELEX}, Annex III(1)",
        ["biometric", "facial recognition", "emotion recognition", "fingerprint"]),
    2: ("Critical infrastructure (safety components)", f"{CELEX}, Annex III(2)",
        ["critical infrastructure", "water supply", "gas supply", "electricity", "road traffic"]),
    3: ("Education and vocational training", f"{CELEX}, Annex III(3)",
        ["education", "exam", "student", "admission", "learning outcome", "proctoring"]),
    4: ("Employment, workers' management, self-employment", f"{CELEX}, Annex III(4)",
        ["recruitment", "hiring", "cv screening", "resume", "job application", "employee monitoring", "promotion"]),
    5: ("Essential private & public services (incl. credit, insurance)", f"{CELEX}, Annex III(5)",
        ["credit scoring", "creditworthiness", "insurance pricing", "benefits eligibility", "emergency dispatch", "triage"]),
    6: ("Law enforcement", f"{CELEX}, Annex III(6)",
        ["law enforcement", "police", "polygraph", "recidivism", "evidence reliability"]),
    7: ("Migration, asylum, border control", f"{CELEX}, Annex III(7)",
        ["migration", "asylum", "border control", "visa", "residence permit"]),
    8: ("Administration of justice & democratic processes", f"{CELEX}, Annex III(8)",
        ["judicial", "court", "adjudicat", "election", "referendum", "voting"]),
}

# --- Art 50 limited-risk (transparency) signals -----------------------------
ART50_SIGNALS = {
    "interacts_with_humans": ("Direct interaction with natural persons (chatbot disclosure)", f"{CELEX}, Art 50(1)"),
    "generates_synthetic_content": ("Generates synthetic audio/image/video/text (marking)", f"{CELEX}, Art 50(2)"),
    "emotion_recognition": ("Emotion recognition (deployer disclosure)", f"{CELEX}, Art 50(3)"),
    "biometric_categorisation": ("Biometric categorisation (deployer disclosure)", f"{CELEX}, Art 50(3)"),
    "deep_fake": ("Deep-fake generation/manipulation (disclosure)", f"{CELEX}, Art 50(4)"),
}

# --- Art 6(3) derogation conditions -----------------------------------------
DEROGATION_CONDITIONS = {
    "narrow_procedural": ("Narrow procedural task", f"{CELEX}, Art 6(3)(a)"),
    "improve_human_result": ("Improves result of a completed human activity", f"{CELEX}, Art 6(3)(b)"),
    "detect_patterns_no_replace": ("Detects patterns without replacing human assessment", f"{CELEX}, Art 6(3)(c)"),
    "preparatory_task": ("Preparatory task to an Annex III assessment", f"{CELEX}, Art 6(3)(d)"),
}

# --- High-risk obligations checklist (Chapter III) --------------------------
# Article numbers verified against the official EU Commission AI Act Service Desk
# (ai-act-service-desk.ec.europa.eu, CELEX 32024R1689), retrieved 2026-07-11 —
# all 14 article->topic mappings confirmed. Labels are our own summaries, not
# claimed-verbatim headings.
HIGH_RISK_OBLIGATIONS = [
    ("Art 9", "Risk management system", f"{CELEX}, Art 9"),
    ("Art 10", "Data and data governance", f"{CELEX}, Art 10"),
    ("Art 11", "Technical documentation", f"{CELEX}, Art 11"),
    ("Art 12", "Record-keeping (logging over the system lifetime)", f"{CELEX}, Art 12"),
    ("Art 13", "Transparency and provision of information to deployers", f"{CELEX}, Art 13"),
    ("Art 14", "Human oversight", f"{CELEX}, Art 14"),
    ("Art 15", "Accuracy, robustness and cybersecurity", f"{CELEX}, Art 15"),
    ("Art 17", "Quality management system", f"{CELEX}, Art 17"),
    ("Art 43", "Conformity assessment", f"{CELEX}, Art 43"),
    ("Art 47", "EU declaration of conformity (Annex V)", f"{CELEX}, Art 47"),
    ("Art 48", "CE marking of conformity", f"{CELEX}, Art 48"),
    ("Art 49", "Registration", f"{CELEX}, Art 49"),
    ("Art 72", "Post-market monitoring by providers", f"{CELEX}, Art 72"),
    ("Art 73", "Reporting of serious incidents", f"{CELEX}, Art 73"),
]

LIMITED_OBLIGATIONS = [
    ("Art 50", "Transparency obligations (disclosure/marking)", f"{CELEX}, Art 50"),
]

DISCLAIMER = (
    "Protocol-layer assessment under Reg (EU) 2024/1689, derived deterministically "
    "from the declared signals and pinned to the cited provisions. Not a legal "
    "determination; the Art 6(3) derogation and Art 6(4) documentation duty may "
    "require legal review."
)


def _kw_hit(text: str, keywords: list[str]) -> str | None:
    t = (text or "").lower()
    for kw in keywords:
        if kw in t:
            return kw
    return None


def classify(
    *,
    use_case: str,
    intended_purpose: str,
    annex_iii_area: int | None = None,
    is_annex_i_safety_component: bool = False,
    requires_third_party_conformity: bool = False,
    performs_profiling: bool = False,
    prohibited_flags: list[str] | None = None,
    derogation_claim: str | None = None,
    interacts_with_humans: bool = False,
    generates_synthetic_content: bool = False,
    emotion_recognition: bool = False,
    biometric_categorisation: bool = False,
    deep_fake: bool = False,
) -> dict:
    """Deterministic first-match-wins classification (spec-fakten §Classification logic)."""
    prohibited_flags = prohibited_flags or []
    text = f"{use_case} {intended_purpose}"
    matched: list[dict] = []
    notes: list[str] = []

    # 1) PROHIBITED — only on an explicit structured flag (Art 5(1)).
    valid_flags = [f for f in prohibited_flags if f in ART5_PROHIBITED]
    if valid_flags:
        for f in valid_flags:
            label, pin = ART5_PROHIBITED[f]
            matched.append({"provision": pin, "reason": label})
        return _result(TIER_PROHIBITED, matched,
                       obligations=[("Art 5", "Practice prohibited — must not be placed on the market or put into service", f"{CELEX}, Art 5(1)")],
                       gaps=["Practice falls under Art 5(1); no conformity path exists — cease/redesign."],
                       notes=notes, annex_iii_area=None, derogation=None)
    # Free-text hint only -> a note, not a verdict.
    hint = None
    for phrase, flag in ART5_KEYWORD_HINTS.items():
        if phrase in text.lower():
            hint = flag
            break
    if hint:
        notes.append(f"Free-text suggests possible Art 5(1) concern ({ART5_PROHIBITED[hint][1]}); "
                     "confirm via prohibited_flags if applicable — human/legal review recommended.")

    # 2) HIGH-RISK — Annex I product-safety route (Art 6(1)).
    if is_annex_i_safety_component and requires_third_party_conformity:
        matched.append({"provision": f"{CELEX}, Art 6(1)",
                        "reason": "Safety component of / is an Annex I product requiring third-party conformity assessment"})
        return _result(TIER_HIGH, matched, HIGH_RISK_OBLIGATIONS,
                       gaps=_high_risk_gaps(), notes=notes, annex_iii_area=None, derogation=None)

    # 3) HIGH-RISK — Annex III route (Art 6(2)), with Art 6(3) derogation.
    area = annex_iii_area
    kw = None
    if area is None:
        for n, (_label, _pin, kws) in ANNEX_III_AREAS.items():
            kw = _kw_hit(text, kws)
            if kw:
                area = n
                break
    if area in ANNEX_III_AREAS:
        label, pin, _ = ANNEX_III_AREAS[area]
        matched.append({"provision": pin, "reason": f"Annex III use-case: {label}"
                        + (f" (matched on '{kw}')" if kw else "")})
        # Art 6(3) derogation — never applies if profiling (Art 6(3) last subpara).
        if derogation_claim in DEROGATION_CONDITIONS and not performs_profiling:
            dlabel, dpin = DEROGATION_CONDITIONS[derogation_claim]
            matched.append({"provision": dpin, "reason": f"Derogation claimed: {dlabel}"})
            notes.append("Art 6(3) derogation claimed — provider MUST document the assessment "
                         "before placing on market and register under Art 49(2) (Art 6(4)).")
            return _result(TIER_LIMITED, matched,
                           obligations=[("Art 6(4)", "Document the not-high-risk assessment + register (Art 49(2))", f"{CELEX}, Art 6(4)")]
                                       + LIMITED_OBLIGATIONS,
                           gaps=["Retain the Art 6(3)/6(4) assessment documentation for national competent authorities."],
                           notes=notes, annex_iii_area=area, derogation=derogation_claim)
        if performs_profiling and derogation_claim:
            notes.append("Derogation NOT available: system performs profiling of natural persons "
                         "(Art 6(3) last subparagraph) — remains high-risk.")
        return _result(TIER_HIGH, matched, HIGH_RISK_OBLIGATIONS,
                       gaps=_high_risk_gaps(), notes=notes, annex_iii_area=area, derogation=None)

    # 4) LIMITED — Art 50 transparency.
    signals = {
        "interacts_with_humans": interacts_with_humans,
        "generates_synthetic_content": generates_synthetic_content,
        "emotion_recognition": emotion_recognition,
        "biometric_categorisation": biometric_categorisation,
        "deep_fake": deep_fake,
    }
    active = [k for k, v in signals.items() if v]
    if active:
        for k in active:
            lbl, pin = ART50_SIGNALS[k]
            matched.append({"provision": pin, "reason": lbl})
        return _result(TIER_LIMITED, matched, LIMITED_OBLIGATIONS,
                       gaps=["Ensure Art 50 disclosure/marking is provided at the latest at first interaction/exposure (Art 50(5))."],
                       notes=notes, annex_iii_area=None, derogation=None)

    # 5) MINIMAL — residual (Art 95).
    matched.append({"provision": f"{CELEX}, Art 95", "reason": "Residual: not prohibited, not high-risk, no Art 50 trigger"})
    return _result(TIER_MINIMAL, matched,
                   obligations=[("Art 95", "Voluntary codes of conduct (no mandatory obligations)", f"{CELEX}, Art 95(1)")],
                   gaps=[], notes=notes, annex_iii_area=None, derogation=None)


def _high_risk_gaps() -> list[str]:
    return [
        "Establish a risk management system (Art 9) and technical documentation (Art 11).",
        "Ensure Art 12 record-keeping (logs over the system lifetime) is enabled.",
        "Complete conformity assessment (Art 43) and draw up the EU declaration of conformity (Art 47 / Annex V) — see POST /compliance/declaration.",
        "Register under Art 49; set up serious-incident reporting readiness (Art 73) — see POST /compliance/incident.",
    ]


def _result(tier, matched, obligations, gaps, notes, annex_iii_area, derogation) -> dict:
    return {
        "risk_tier": tier,
        "matched_provisions": matched,
        "annex_iii_area": annex_iii_area,
        "derogation_claimed": derogation,
        "obligations": [{"article": a, "obligation": o, "provision": p} for (a, o, p) in obligations],
        "gap_analysis": gaps,
        "notes": notes,
        "disclaimer": DISCLAIMER,
        "spec_source": "docs/spec-fakten/eu-ai-act-2024-1689.md (CELEX 32024R1689, 2026-07-11)",
    }


# --- Annex V declaration builder (→ VC credentialSubject) -------------------
def build_declaration_claims(
    *,
    ai_system_name: str,
    ai_system_reference: str,
    provider_name: str,
    provider_address: str,
    conformity_with_other_union_law: str | None = None,
    processes_personal_data: bool = False,
    harmonised_standards: list[str] | None = None,
    notified_body: dict | None = None,
    place_of_issue: str,
    signatory_name: str,
    signatory_function: str,
    on_behalf_of: str,
) -> dict:
    """Build the Annex-V 8-field structure for a MolTrustConformityDeclaration VC.

    Fields map 1:1 to Annex V(1)–(8) (spec-fakten §Annex V).
    """
    today = datetime.date.today().isoformat()
    gdpr_stmt = None
    if processes_personal_data:
        gdpr_stmt = ("The AI system complies with Regulation (EU) 2016/679, Regulation (EU) 2018/1725 "
                     "and Directive (EU) 2016/680.")
    return {
        "declarationStandard": "EU AI Act Annex V (Reg (EU) 2024/1689, Art 47)",
        # Annex V(1)
        "aiSystem": {"name": ai_system_name, "type": "AI system", "reference": ai_system_reference},
        # Annex V(2)
        "provider": {"name": provider_name, "address": provider_address},
        # Annex V(3)
        "soleResponsibilityStatement":
            "This EU declaration of conformity is issued under the sole responsibility of the provider.",
        # Annex V(4)
        "conformityStatement":
            "The AI system is in conformity with Regulation (EU) 2024/1689"
            + (f" and with {conformity_with_other_union_law}" if conformity_with_other_union_law else "") + ".",
        # Annex V(5)
        "dataProtectionStatement": gdpr_stmt,
        # Annex V(6)
        "harmonisedStandards": harmonised_standards or [],
        # Annex V(7)
        "notifiedBody": notified_body,  # {name, id, procedure, certificate} or None
        # Annex V(8)
        "issuance": {
            "place": place_of_issue,
            "date": today,
            "signatory": {"name": signatory_name, "function": signatory_function},
            "onBehalfOf": on_behalf_of,
        },
        "annexVComplete": True,
    }


# --- HTML report renderer ---------------------------------------------------
def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def render_report_html(*, did: str, identity: dict, assessment: dict | None,
                       declarations: list[dict], trust_score: dict | None,
                       audit_summary: dict) -> str:
    """Render the GET /compliance/report/{did} HTML v1 report."""
    def rows(pairs):
        return "".join(
            f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in pairs
        )

    tier = (assessment or {}).get("risk_tier")
    tier_badge = f'<span class="tier tier-{_esc(tier)}">{_esc(tier or "not assessed")}</span>' if True else ""

    obligations_html = "<p>No assessment on record — call <code>POST /compliance/assess</code>.</p>"
    gaps_html = ""
    provisions_html = ""
    if assessment:
        obligations_html = "<ul>" + "".join(
            f"<li><strong>{_esc(o['article'])}</strong> — {_esc(o['obligation'])} "
            f"<span class='pin'>{_esc(o['provision'])}</span></li>"
            for o in assessment.get("obligations", [])
        ) + "</ul>"
        gaps_html = "<ul>" + "".join(f"<li>{_esc(g)}</li>" for g in assessment.get("gap_analysis", [])) + "</ul>" \
            if assessment.get("gap_analysis") else "<p>No open gaps recorded.</p>"
        provisions_html = "<ul>" + "".join(
            f"<li>{_esc(m['reason'])} <span class='pin'>{_esc(m['provision'])}</span></li>"
            for m in assessment.get("matched_provisions", [])
        ) + "</ul>"

    decl_html = "<p>No conformity declarations on record.</p>"
    if declarations:
        decl_html = "<ul>" + "".join(
            f"<li>{_esc(d.get('credential_type'))} — issued {_esc(d.get('issued_at'))}, "
            f"expires {_esc(d.get('expires_at'))}</li>" for d in declarations
        ) + "</ul>"

    ts_html = "<p>No trust score available.</p>"
    if trust_score:
        ts_html = "<table class='kv'>" + rows([
            ("Trust score", trust_score.get("score")),
            ("Total ratings", trust_score.get("total_ratings")),
        ]) + "</table>"

    generated = datetime.datetime.utcnow().isoformat() + "Z"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MolTrust Compliance Report — {_esc(did)}</title>
<style>
:root{{color-scheme:light dark}}
body{{font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:2rem;max-width:900px;margin-inline:auto;color:#111}}
@media(prefers-color-scheme:dark){{body{{background:#0d1117;color:#e6edf3}}th{{color:#9da7b3}}.card{{background:#161b22;border-color:#30363d}}}}
h1{{font-size:1.5rem;margin:0 0 .25rem}}h2{{font-size:1.1rem;margin:1.5rem 0 .5rem;border-bottom:1px solid #8884;padding-bottom:.25rem}}
.card{{border:1px solid #d0d7de;border-radius:10px;padding:1rem 1.25rem;margin:1rem 0;background:#f6f8fa}}
table.kv{{border-collapse:collapse;width:100%}}table.kv th{{text-align:left;width:220px;vertical-align:top;color:#57606a;font-weight:600;padding:.25rem .5rem}}table.kv td{{padding:.25rem .5rem}}
.pin{{font:12px ui-monospace,monospace;color:#57606a;background:#8881;padding:.05rem .35rem;border-radius:4px}}
.tier{{display:inline-block;padding:.15rem .6rem;border-radius:999px;font-weight:700;text-transform:uppercase;font-size:.8rem}}
.tier-prohibited{{background:#cf222e;color:#fff}}.tier-high{{background:#bc4c00;color:#fff}}.tier-limited{{background:#9a6700;color:#fff}}.tier-minimal{{background:#1a7f37;color:#fff}}
footer{{margin-top:2rem;color:#57606a;font-size:.85rem}}
</style></head><body>
<h1>MolTrust EU AI Act Compliance Report</h1>
<p><code>{_esc(did)}</code></p>
<div class="card"><h2 style="border:0;margin-top:0">Risk classification {tier_badge}</h2>
<table class="kv">{rows([
    ("Display name", identity.get("display_name")),
    ("Agent class", identity.get("agent_class")),
    ("Framework", identity.get("agent_framework")),
    ("Publisher", identity.get("publisher")),
])}</table></div>
<h2>Matched provisions</h2><div class="card">{provisions_html}</div>
<h2>Obligations</h2><div class="card">{obligations_html}</div>
<h2>Gap analysis</h2><div class="card">{gaps_html}</div>
<h2>Conformity declarations</h2><div class="card">{decl_html}</div>
<h2>Trust score</h2><div class="card">{ts_html}</div>
<h2>Audit summary</h2><div class="card"><table class="kv">{rows(list(audit_summary.items()))}</table></div>
<footer>Generated {generated} · Source: EU AI Act (Reg (EU) 2024/1689, CELEX 32024R1689) ·
Protocol-layer assessment pinned to docs/spec-fakten/eu-ai-act-2024-1689.md — not a legal determination.</footer>
</body></html>"""
