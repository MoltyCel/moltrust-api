"""MolTrust Scout Agent - Trend Detection for the Agent Economy

What the briefing says about MolTrust comes only from agents/scout_facts.md,
read on every run (ops SCOUT-FIX, 17.11.2026). Until then the prompt carried a
fixed self-description from the initial commit ("W3C DID:web identity ...
Lightning payments, GitHub OAuth"), and the briefings repeated it as fact.
After generation, fact_violations() checks the text; a hit triggers one
regeneration, and a hit that survives it is marked in the line, not sent
silently.
"""

import os, re, sys, json, datetime, httpx, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import notify  # noqa: E402

AGENT_DID = "did:moltrust:28a0984ab85d4c40"
AGENT_NAME = "MolTrust Scout"
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LOG_DIR = os.path.expanduser("~/moltstack/logs")
FACTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scout_facts.md")

# Search stays on Haiku: five short lookups per run, summarised with the
# cited URLs attached. The briefing moved to Sonnet 5.5: keeping each figure
# with the project its source names is the step that failed on 17.11.
# (XRP Ledger counts reported as x402 scale).
SEARCH_MODEL = "claude-haiku-4-5-20251001"
BRIEFING_MODEL = "claude-sonnet-5-5"

SEARCH_TOPICS = [
    "agent economy trust layer 2026",
    "ERC-8004 trustless agents",
    "AI agent identity verification DID",
    "agentic commerce payments x402",
    "AI agent reputation system",
]


def load_facts(path=FACTS_PATH):
    """The model-facing part of the fact sheet: everything from the first `##`.

    Raises when the file is missing or has no section. There is no built-in
    fallback description; a briefing without the sheet is not produced.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    i = text.find("\n## ")
    if i < 0:
        raise ValueError(f"{path}: no '## ' section")
    return text[i + 1:].strip()


def _sources(content):
    """Title and URL of every source the search answer cited, in order, once."""
    seen, out = set(), []
    for b in content:
        for c in b.get("citations") or []:
            url = c.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append(f"{c.get('title') or url} <{url}>")
    if not out:
        for b in content:
            if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
                for r in b["content"]:
                    url = r.get("url")
                    if url and url not in seen:
                        seen.add(url)
                        out.append(f"{r.get('title') or url} <{url}>")
    return out


def search_web(query):
    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": SEARCH_MODEL,
                "max_tokens": 1000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": (
                    f"Search for latest news about: {query}. Focus on last 7 days. "
                    "Brief summary only. For every figure, name the project or "
                    "company it belongs to and the publication that reports it."
                )}],
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            content = resp.json().get("content", [])
            texts = [b["text"] for b in content if b.get("type") == "text"]
            sources = _sources(content)
            if sources:
                texts.append("Sources: " + "; ".join(sources))
            return texts
        return [f"Search error: {resp.status_code}"]
    except Exception as e:
        return [f"Search failed: {e}"]


def build_prompt(findings, facts, feedback=None):
    prompt = f"""You are the MolTrust Scout Agent (DID: {AGENT_DID}).
Produce a concise intelligence briefing for the MolTrust team.

What MolTrust is comes only from the fact sheet below. Do not attribute to
MolTrust any capability, payment rail, identity method, integration or number
that the sheet does not state. Its section "Never state these as MolTrust
properties" lists claims earlier briefings got wrong; none of them may appear
as a MolTrust property. Where a finding suggests MolTrust could add something,
call it a possible addition, never an existing feature.

<fact_sheet>
{facts}
</fact_sheet>

Search findings (one block per search; the sources each answer cited are
listed under it):

<findings>
"""
    for topic, results in findings.items():
        prompt += f"\n## {topic}\n"
        for r in results:
            prompt += f"{r}\n"

    prompt += """</findings>

Rules for figures:
- Every figure (count, volume, amount, percentage) names its source from the
  findings in brackets, e.g. [Coindesk] or the URL listed there.
- A figure belongs to the project, company, chain or ledger its source names.
  Never attribute one project's figure to another: a count of XRP Ledger
  transactions is not an x402 figure unless the source says exactly that. If
  the findings leave the attribution unclear, leave the figure out.
- No figure that is not in the findings. No figure about MolTrust that is not
  in the fact sheet.

Briefing format:
1. TOP TRENDS - What's moving in the agent economy?
2. OPPORTUNITIES - What can MolTrust capitalize on?
3. THREATS - Competitors or standards to watch?
4. ACTION ITEMS - What should we do this week?

Be concise and actionable."""
    if feedback:
        prompt += ("\n\nA previous draft broke the fact sheet in these lines; "
                   "write the briefing again without these claims:\n"
                   + "\n".join(f"- {v['rule']}: {v['line']}" for v in feedback))
    return prompt


def generate_briefing(findings, facts, feedback=None):
    prompt = build_prompt(findings, facts, feedback)
    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": BRIEFING_MODEL,
                "max_tokens": 8000,
                "output_config": {"effort": "medium"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=180.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("stop_reason") == "refusal":
                return "Briefing failed: refusal"
            text = "\n".join(b["text"] for b in data.get("content", []) if b.get("type") == "text").strip()
            if not text:
                return f"Briefing failed: empty answer (stop_reason={data.get('stop_reason')})"
            return text
        return f"Briefing failed: {resp.status_code}"
    except Exception as e:
        return f"Briefing failed: {e}"


# --- Check after generation ---------------------------------------------
#
# Lightning and GitHub OAuth are flagged wherever they appear; neither is a
# MolTrust property (scout_facts.md) and the briefing has no reason to name
# them. did:web is flagged only where it is said of MolTrust: in a line that
# names MolTrust or "we/our/us", or in OPPORTUNITIES / ACTION ITEMS, which
# are about MolTrust by construction. A line pairing an XRP Ledger figure
# with x402 is flagged as a mixed-up attribution. Negated clauses ("no
# Lightning", "not did:web") are removed before matching (CLAUDE.md, "Eine
# Verneinung ist keine Bejahung").

_NEGATION = re.compile(r"\b(?:no|not|never|without|nor|instead of|rather than)\b[^.;:,()]*", re.I)
_LIGHTNING = re.compile(r"\blightning\b", re.I)
_GITHUB_OAUTH = re.compile(r"\bgithub[\s-]*(?:oauth|sign[\s-]?in|login)\b|\boauth\b[^.;]*\bgithub\b", re.I)
_DID_WEB = re.compile(r"\bdid:web\b", re.I)
_MOLTRUST_CONTEXT = re.compile(r"\bmoltrust\b|\bwe\b|\bour\b|\bus\b", re.I)
_XRP = re.compile(r"\bxrp(?:l)?\b|\bripple\b", re.I)
_X402 = re.compile(r"\bx402\b", re.I)
_SECTION = re.compile(r"^\W*(?:\d+\.\s*)?\W*(TOP TRENDS|OPPORTUNITIES|THREATS|ACTION ITEMS)\b")
_MOLTRUST_SECTIONS = {"OPPORTUNITIES", "ACTION ITEMS"}

MARK = "⚠️ FACT CHECK"


def fact_violations(text):
    """Lines that state a forbidden claim. [] for a clean briefing."""
    out, section = [], None
    for n, line in enumerate(text.splitlines(), 1):
        m = _SECTION.match(line)
        if m:
            section = m.group(1).upper()
        if line.startswith(MARK):
            continue
        clean = _NEGATION.sub(" ", line)
        rules = []
        if _LIGHTNING.search(clean):
            rules.append("Lightning is not a MolTrust payment rail")
        if _GITHUB_OAUTH.search(clean):
            rules.append("GitHub OAuth is not a MolTrust feature")
        if _DID_WEB.search(clean) and (_MOLTRUST_CONTEXT.search(clean) or section in _MOLTRUST_SECTIONS):
            rules.append("did:web is not MolTrust's identity method (did:moltrust)")
        if _XRP.search(clean) and _X402.search(clean) and re.search(r"\d", _X402.sub(" ", clean)):
            rules.append("XRP Ledger figure attributed to x402")
        for r in rules:
            out.append({"line_no": n, "rule": r, "line": line.strip()})
    return out


def mark_violations(text, violations):
    """Prefix every offending line with the fact-check mark and its rules."""
    by_line = {}
    for v in violations:
        by_line.setdefault(v["line_no"], []).append(v["rule"])
    lines = text.splitlines()
    for n, rules in by_line.items():
        lines[n - 1] = f"{MARK} ({'; '.join(rules)}): {lines[n - 1]}"
    lines.append("")
    lines.append(f"{MARK}: {len(by_line)} line(s) above contradict agents/scout_facts.md "
                 "after one regeneration; treat them as unverified.")
    return "\n".join(lines)


def produce_briefing(findings, facts, generate=None):
    """Generate, check, regenerate once on a hit, mark what survives.

    Returns (briefing, status) with status one of "clean", "regenerated",
    "marked", "failed".
    """
    generate = generate or generate_briefing
    briefing = generate(findings, facts)
    if briefing.startswith("Briefing failed"):
        return briefing, "failed"
    hits = fact_violations(briefing)
    if not hits:
        return briefing, "clean"
    print(f"Fact check: {len(hits)} hit(s), regenerating once")
    second = generate(findings, facts, feedback=hits)
    if second.startswith("Briefing failed"):
        return mark_violations(briefing, hits), "marked"
    hits2 = fact_violations(second)
    if not hits2:
        return second, "regenerated"
    print(f"Fact check: {len(hits2)} hit(s) after regeneration, marking lines")
    return mark_violations(second, hits2), "marked"


def run():
    now = datetime.datetime.now(datetime.UTC)
    print(f"\n{'='*60}")
    print(f"MOLTRUST SCOUT AGENT")
    print(f"DID: {AGENT_DID}")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    if not API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return

    try:
        facts = load_facts()
    except (OSError, ValueError) as e:
        print(f"ERROR: fact sheet unreadable: {e}")
        melden(now, f"Briefing failed: fact sheet unreadable ({e})")
        return

    print("Phase 1: Scanning trends...\n")
    findings = {}
    for topic in SEARCH_TOPICS:
        print(f"  Searching: {topic}...")
        results = search_web(topic)
        findings[topic] = results
        time.sleep(5)

    print("\nPhase 2: Generating briefing...\n")
    time.sleep(5)
    briefing, check = produce_briefing(findings, facts)
    print(f"Fact check: {check}")

    date_str = now.strftime("%Y%m%d_%H%M")
    report_path = os.path.join(LOG_DIR, f"scout_{date_str}.md")

    report = f"""# MolTrust Scout Briefing
**Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}
**Agent:** {AGENT_NAME} ({AGENT_DID})
**Model:** {BRIEFING_MODEL} · **Fact check:** {check}

---

{briefing}

---

*Generated by MolTrust Scout Agent*
"""
    with open(report_path, "w") as f:
        f.write(report)

    print(briefing)
    print(f"\nReport saved: {report_path}")
    melden(now, briefing)


def melden(now, briefing):
    """Market Watch reaches Lars on every run (ops MELD-BERICHT, 12.11.2026).

    Until then this run only wrote logs/scout_<date>.md and never sent
    anything. A failed briefing is a pipeline error and is filed, not sent.
    """
    if briefing.startswith("Briefing failed"):
        notify.send_telegram(f"Market Watch: {briefing}", channel=notify.WORKLOG,
                             art="pipeline-fehler")
        return
    text = f"📈 Market Watch — MolTrust Scout Briefing, {now:%Y-%m-%d %H:%M} UTC\n\n{briefing}"
    if not notify.send_telegram(text, channel=notify.WORKLOG, chunk=True, art="scout-bericht"):
        print(f"Telegram not sent ({notify.letzter_grund() or 'failed'})")


if __name__ == "__main__":
    run()
