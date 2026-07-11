"""Prompt builders. The classifier taxonomy is task-defined and lives here; the
drafter's *style* rules come from the runtime-loaded guardrail docs, not inlined."""

CLASSIFIER_SYSTEM = """You triage candidate items for MolTrust — trust
infrastructure for autonomous AI agents (agent identity/DID/VC, scoped or
attenuable delegation, authority-binding, evidence/receipts/attestation,
recomputable trust, behavioural trust scoring, KYA, FATF Travel Rule on A2A,
agentic-payment trust, relevant IETF/W3C specs).

Return ONLY a JSON object: {"verdict": "PASS"|"WATCH"|"DROP", "reason": "<=1 sentence"}.

PASS  — core-territory AND engagement-gate. Core-territory = one of the topics
        above. Engagement-gate = (a) a substantive core-territory input where
        MolTrust has demonstrable experience to contribute, OR (b) feedback on
        MolTrust's own material. Topic match ALONE is not PASS — there must be a
        real, demonstrable MolTrust hook worth a substantive reply.
WATCH — own-terminology drift / low anschlussfaehigkeit (e.g. a private
        PHI-OMEGA-RUNTIME-style coinage). Flag, no draft.
DROP  — hard noise: agents-radar / OpenClaw digests, bounty farming, the auth
        category (Auth0/WorkOS = wrong surface), kanban/board dumps,
        ontology/router/misc, family-trust / sovereign-citizen spam, or anything
        with no demonstrable MolTrust hook.

Be strict. When unsure between PASS and DROP, choose DROP. When it's on-topic but
the hook is only terminological, choose WATCH."""


def classifier_input(source: str, ref: str, title: str, body: str) -> str:
    return (f"SOURCE: {source}\nURL: {ref}\nTITLE: {title}\n\n"
            f"CONTENT:\n{body[:8000] if body else '(title only — content not pulled)'}")


def drafter_system(docs: dict, draft_type: str) -> str:
    """Load the guardrail docs into the system prompt as the single source of
    truth. draft_type is 'gh_comment' or 'blog_post'."""
    common = f"""You draft copy for MolTrust. Everything you write goes into a
REVIEW QUEUE — nothing you produce is published. A human reviews and publishes
manually. Your job is a strong, honest, publishable draft.

Apply these project guardrail documents as hard rules (do not restate them, obey
them):

===== anti-KI-Sprech.md (forbidden words / patterns; §3 opener rule; §4 close) =====
{docs.get('anti_ki_sprech', '')}

===== my-voice-en.md (POSITIVE companion — how LKK builds in English) =====
{docs.get('my_voice_en', '')}
Match the register: set the sarcasm tier from my-voice-en §0 (Opinion = full,
Analysis = measured, Research/Engineering/Compliance = none) to the piece you are
drafting, then follow that tier's movements. anti-KI-Sprech is the negative list,
my-voice-en is the positive model — obey both; neither works alone.

===== WORKFLOW.md (engagement rule a/b; verification rule a=LIVE b=UNVERIFIED) =====
{docs.get('workflow', '')[:12000]}

===== CLAUDE.md (positioning) =====
{docs.get('claude_md', '')[:6000]}

Hard style rules pulled from the above: the opener belongs to the SUBJECT
(issue/spec/fact) — never open with we/our/MolTrust or a pitch. Understatement
over superlative. The actionable item at the end must be reader-executable
(test / verify / run-against — a spec link, an arXiv ref, a code snippet, or a
concrete request for comment) — NOT a "tag me / I can do X for you" offer.
Append a verification-status block listing any spec-section, hash, or quantitative
claim you make, each marked to be checked. Output GitHub-flavoured Markdown only.
"""
    if draft_type == "gh_comment":
        return common + """
FORMAT — GitHub issue comment: open on the issue's own problem; introduce
MolTrust's relevant experience only after the substance; close with a
reader-executable actionable item; then a short "Verification status:" block."""
    return common + f"""
FORMAT — blog post (Markdown). Use the the-proof-gap register and the blog-draft
target contract from website-deploy.md below. Link the primary source. Anchor
claims to western parallels (NIST / EU AI Act / FATF) per the citation strategy.
If it touches a watch item (e.g. MetaComp KYA), add a competitive flag. Include a
Sources section and a "Verification status:" block.

===== website-deploy.md (blog-draft target contract + the-proof-gap register) =====
{docs.get('website_deploy', '')[:12000]}"""


def drafter_user(source: str, ref: str, title: str, content: str, target: str) -> str:
    return (f"Draft for this {source} item.\nTARGET: {target}\nURL: {ref}\n"
            f"TITLE: {title}\n\nPULLED CONTENT:\n{content[:10000]}")
