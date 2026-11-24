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
# While this file exists on the server, briefings are written to the log but
# not sent to Lars (ops SCOUT-FIX-W, 18.11.2026: "Briefings bis zur Abnahme
# nicht an Lars weiterleiten"). A briefing whose fact check still fails is
# filed as a pipeline error for the Console instead. Remove the file on
# acceptance; sending then resumes without a code change.
HOLD_PATH = os.path.expanduser("~/moltstack/data/scout_hold")

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
    self_test(sheet_rules(text))
    return text[i + 1:].strip()


# --- Rules from the fact sheet (ops SCOUT-FIX-2311, 23.11.2026) -----------
#
# The corrections of 22.11.2026 never reached the briefing: they were given in
# the chat, but no one wrote them into scout_facts.md, and even an entry there
# would only have been prompt text. The check below was code alone, so a new
# fact had no check. Since SCOUT-FIX-2311 the sheet carries its own checks
# under "## Rules the check enforces"; fact_violations() applies them, and
# every run first tries each rule on its own example. A rule that does not
# fire on its example, or a line that cannot be read, stops the run before a
# briefing is generated.

_RULE_LINE = re.compile(r"^- check:\s*(.+)$")
_RULES_HEADING = "## Rules the check enforces"


def sheet_rules(text):
    """The `- check:` lines of the sheet as dicts. Raises on a malformed line."""
    i = text.find(_RULES_HEADING)
    if i < 0:
        return []
    body = text[i + len(_RULES_HEADING):]
    j = body.find("\n## ")
    body = body if j < 0 else body[:j]
    rules = []
    for raw in body.splitlines():
        m = _RULE_LINE.match(raw.strip())
        if not m:
            continue
        fields = {}
        kind, *rest = [f.strip() for f in m.group(1).split(" | ")]
        for f in rest:
            key, sep, val = f.partition(":")
            if not sep:
                raise ValueError(f"fact sheet rule: field without ':' in {raw!r}")
            fields[key.strip()] = val.strip()
        missing = {"match", "example", "why"} - fields.keys()
        if missing:
            raise ValueError(f"fact sheet rule: missing {sorted(missing)} in {raw!r}")
        rule = {"why": fields["why"], "example": fields["example"],
                "match": re.compile(fields["match"], re.I)}
        if kind == "block":
            rule["kind"] = "block"
        elif kind.startswith("not-in "):
            rule["kind"] = "not-in"
            rule["sections"] = {x.strip().upper() for x in kind[7:].split(",") if x.strip()}
            unknown = rule["sections"] - _SECTIONS
            if unknown or not rule["sections"]:
                raise ValueError(f"fact sheet rule: unknown section {sorted(unknown)} in {raw!r}")
        elif kind.startswith("covered "):
            rule["kind"] = "covered"
            rule["job"] = kind[8:].strip()
            rule["sections"] = {"ACTION ITEMS"}
            rule["why"] = f"Already covered ({rule['job']}): {fields['why']}"
            if not rule["job"]:
                raise ValueError(f"fact sheet rule: covered without a job in {raw!r}")
        elif kind.startswith("requires "):
            rule["kind"] = "requires"
            rule["requires"] = [re.compile(x.strip(), re.I) for x in kind[9:].split(";") if x.strip()]
            if not rule["requires"]:
                raise ValueError(f"fact sheet rule: requires without a pattern in {raw!r}")
        else:
            raise ValueError(f"fact sheet rule: unknown kind {kind!r} in {raw!r}")
        rules.append(rule)
    return rules


def _rule_hits(rule, clean, section):
    if not rule["match"].search(clean):
        return False
    if rule["kind"] == "block":
        return True
    if rule["kind"] in ("not-in", "covered"):
        return section in rule["sections"]
    return not all(r.search(clean) for r in rule["requires"])


def self_test(rules):
    """Every rule fails on its own example, in a section where it applies.

    A `covered` rule must also be removed by drop_covered_actions(), leaving
    the section with NO_NEW_ACTIONS.
    """
    for rule in rules:
        sections = sorted(rule.get("sections") or {"TOP TRENDS"})
        for sec in sections:
            text = f"## {sec}\n{rule['example']}\n"
            if not any(v["rule"] == rule["why"] for v in fact_violations(text, rules=[rule])):
                raise ValueError(f"fact sheet rule does not fire on its own example "
                                 f"in {sec}: {rule['why']}")
        if rule["kind"] == "covered":
            out = drop_covered_actions(f"## 4. ACTION ITEMS\n{rule['example']}\n", rules=[rule])
            if NO_NEW_ACTIONS not in out or rule["example"].strip() in out:
                raise ValueError(f"covered rule does not remove its own example: {rule['why']}")
    return len(rules)


# --- Action items a job already covers (Lars 24.11.2026) -------------------
#
# The briefing of 24.11. proposed four action items that were already jobs or
# decisions (x401 comparison, FG-TIDA contribution, x402 in front of AWS,
# ERC-8004 link-not-mint). Items matching a `covered` rule of the sheet are
# removed before the check; the remaining items are renumbered, and an empty
# section says NO_NEW_ACTIONS.

NO_NEW_ACTIONS = "No new action items."
_ITEM = re.compile(r"^ {0,3}(?:(\d+)[.)]|[-*•])\s+")


def drop_covered_actions(text, rules=None):
    covered = [r for r in (_rules_from_sheet() if rules is None else rules) if r["kind"] == "covered"]
    if not covered:
        return text
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines)
                  if _SECTION.match(ln) and _SECTION.match(ln).group(1).upper() == "ACTION ITEMS"), None)
    if start is None:
        return text
    end = next((i for i in range(start + 1, len(lines)) if _SECTION.match(lines[i])), len(lines))
    head, body, tail = lines[:start + 1], lines[start + 1:end], lines[end:]
    # blocks: an item is its first line plus the indented lines under it;
    # every other line is a block of its own and stays
    blocks = []
    for ln in body:
        if _ITEM.match(ln):
            blocks.append(("item", [ln]))
        elif ln.strip() and ln[:1].isspace() and blocks and blocks[-1][0] == "item":
            blocks[-1][1].append(ln)
        else:
            blocks.append(("other", [ln]))
    kept, n, items_left, dropped = [], 0, False, False
    for kind, block in blocks:
        if kind == "item":
            clean = " ".join(_NEGATION.sub(" ", ln) for ln in block)
            if any(r["match"].search(clean) for r in covered):
                dropped = True
                continue
            items_left = True
            m = _ITEM.match(block[0])
            if m.group(1):
                n += 1
                block = [f"{n}." + block[0][m.end(1) + 1:]] + block[1:]
        kept.extend(block)
    if not dropped and items_left:
        return text
    if not items_left:
        kept = [NO_NEW_ACTIONS]
    while kept and not kept[-1].strip():
        kept.pop()
    while kept and not kept[0].strip():
        kept.pop(0)
    if tail or text.endswith("\n"):
        kept.append("")
    return "\n".join(head + kept + tail)


def _rules_from_sheet():
    try:
        with open(FACTS_PATH, encoding="utf-8") as f:
            return sheet_rules(f.read())
    except OSError:
        return []


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
- Do not combine XRP Ledger and x402 in one sentence with a figure, even
  with a source; on 17.11.2026 exactly that turned XRP Ledger counts into
  x402 scale. Report either one on its own, or leave the figure out.
- No figure that is not in the findings. No figure about MolTrust that is not
  in the fact sheet.
- Every figure needs a primary source and its date, both in the line, e.g.
  "(Chainalysis, 14.10.2026) [url]". A primary source is the organisation that
  measured or announced the figure. A blog, newsletter, aggregator or tracker
  that repeats it is not one. No primary source with a date: leave it out.
- x402 scale only as the fact sheet gives it (Chainalysis, 138 million x402
  transactions on Base, 14.10.2026). No other x402 totals, no multi-chain
  tracker counts.
- No XRP Ledger figures at all.
- Members of a coalition, consortium or alliance only with the primary
  announcement as source; otherwise name the body without its members.
- ERC-8004 is an interop partner (fact sheet). Never list it under THREATS.

Briefing format, four sections:
1. TOP TRENDS: what is moving in the agent economy
2. OPPORTUNITIES: what MolTrust can capitalize on
3. THREATS: competitors or standards to watch
4. ACTION ITEMS: what we should do this week. Only items that no job or
   decision covers yet: the `covered` rules in the fact sheet list the ones
   that do, and such items are removed before the briefing goes out. If no
   new item remains, the section reads "No new action items."

Every section heading stands alone on its own line, written as
"## 1. TOP TRENDS", "## 2. OPPORTUNITIES", "## 3. THREATS",
"## 4. ACTION ITEMS". Nothing else goes on that line; the section's text
starts on the next line.

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
# with x402 is flagged as a mixed-up attribution.
#
# Added by SCOUT-FIX-W (briefing 18.11.2026 18:00, clean by the old check):
# ERC-8004 anywhere in THREATS; an x402 total other than Chainalysis' 138M on
# Base; any XRP Ledger figure; a membership claim (coalition, alliance,
# member, joined) without a source whose host names one of the organisations
# in the line; and any figure without a bracketed source and a date in the
# same line.
#
# Added by SCOUT-FIX-N (Lars, 19.11.2026): a line naming Chainalysis, 138
# million, Base and 14.10.2026 is sourced without a link (the 18:00 briefing
# of 19.11. had it marked as "Figure without source and date"); the x402 rule
# now also wants Base, so a 138M figure for another chain is red. Negated
# clauses ("no
# Lightning", "not did:web") are removed before matching (CLAUDE.md, "Eine
# Verneinung ist keine Bejahung").

_NEGATION = re.compile(r"\b(?:no|not|never|without|nor|instead of|rather than)\b[^.;:,()]*", re.I)
_LIGHTNING = re.compile(r"\blightning\b", re.I)
_GITHUB_OAUTH = re.compile(r"\bgithub[\s-]*(?:oauth|sign[\s-]?in|login)\b|\boauth\b[^.;]*\bgithub\b", re.I)
_DID_WEB = re.compile(r"\bdid:web\b", re.I)
_MOLTRUST_CONTEXT = re.compile(r"\bmoltrust\b|\bwe\b|\bour\b|\bus\b", re.I)
_XRP = re.compile(r"\bxrp(?:l)?\b|\bripple\b", re.I)
_X402 = re.compile(r"\bx402\b", re.I)
_ERC8004 = re.compile(r"\berc[\s-]?8004\b|\b8004scan\b", re.I)
# A figure worth a source: 4+ digits, a thousands separator, a percentage, a
# currency amount, or a number with million/billion/M/K/bn. Plain small
# numbers ("12 chains", "Nov 12", "ERC-8004") are not figures by themselves.
_FIGURE = re.compile(
    r"(?<![\w-])(?:\$\s?\d[\d,.]*\s?(?:[kmb]n?|million|billion)?\b"
    r"|\d{1,3}(?:,\d{3})+\b|\d+(?:\.\d+)?\s?%"
    r"|\d+(?:\.\d+)?\s?(?:million|billion|mio\.?|bn|[mk])\b\+?"
    r"|\d{5,}\b)", re.I)
_CHAINALYSIS_138 = re.compile(r"chainalysis", re.I)
_138M = re.compile(r"\b138(?:\.\d+)?\s?(?:million|mio\.?|m)\b|\b138,\d{3},\d{3}\b", re.I)
_BASE = re.compile(r"\bbase\b", re.I)
_DATE_14_10_2026 = re.compile(
    r"\b14\.\s?10\.\s?2026\b|\b2026-10-14\b"
    r"|\boct(?:ober)?\.?\s+14(?:th)?,?\s+2026\b|\b14(?:th)?\s+oct(?:ober)?\.?,?\s+2026\b", re.I)
_CITATION = re.compile(r"\[([^\]]+)\]|<(https?://[^>]+)>|\((https?://[^)]+)\)")
_DATE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b"
    r"|\b\d{1,2}\.\d{1,2}\.\d{4}\b|\b\d{4}[-/]\d{2}[-/]\d{2}\b"
    r"|\b(?:q[1-4]|h[12])\s?\d{4}\b", re.I)
_MEMBERSHIP = re.compile(r"\b(?:coalition|consortium|alliance|members?|membership|joined|joins|co-?founded|founding)\b", re.I)
# Two named organisations joined by "and", "/" or ",": "Cloudflare and Tools
# for Humanity", "Cloudflare/Tools for Humanity".
_ORG_PAIR = re.compile(r"\b[A-Z][\w.-]+(?:\s+(?:for|of)\s+[A-Z][\w-]+)?\s*(?:/|,|\band\b)\s*[A-Z][\w-]+")
_SENTENCE = re.compile(r"(?<=[.;])\s+(?=[A-Z*])")
_SECTION = re.compile(r"^\W*(?:\d+\.\s*)?\W*(TOP TRENDS|OPPORTUNITIES|THREATS|ACTION ITEMS)\b")
_MOLTRUST_SECTIONS = {"OPPORTUNITIES", "ACTION ITEMS"}
_SECTIONS = {"TOP TRENDS", "OPPORTUNITIES", "THREATS", "ACTION ITEMS"}
# A section name written as a heading: upper case, optionally numbered. Used
# to put every heading on its own line (ops SCOUT-FIX-N, 19.11.2026).
_SECTION_NAME = re.compile(r"(?:\d\.\s*)?(?:TOP TRENDS|OPPORTUNITIES|THREATS|ACTION ITEMS)\b")
_HEADING_HEAD = re.compile(
    r"^(\s*(?:#{1,6}\s*)?(?:\*\*)?(?:\d\.\s*)?"
    r"(?:TOP TRENDS|OPPORTUNITIES|THREATS|ACTION ITEMS)\b(?:\s*\([^)]*\))?(?:\*\*)?)"
    r"\s*(?:[:\u2013\u2014-]\s+|:)(\S.*)$")

MARK = "⚠️ FACT CHECK"


def _chainalysis_line(clean):
    """The fact-sheet figure stated in full: Chainalysis, 138 million, Base and
    14.10.2026 in one line. Such a line is sourced without a link (Lars,
    19.11.2026, ops SCOUT-FIX-N); missing any of the four, it is not."""
    return all(p.search(clean) for p in (_CHAINALYSIS_138, _138M, _BASE, _DATE_14_10_2026))


def headings_on_own_line(text):
    """Every section heading on a line of its own (ops SCOUT-FIX-N).

    "1. TOP TRENDS - x402 is growing" becomes two lines, and a heading that
    starts in the middle of a line ("... done. 2. OPPORTUNITIES") is moved to
    the next one. Section names in lower or mixed case are prose and stay.
    """
    out = []
    for line in text.split("\n"):
        rest = line
        while True:
            m = next((m for m in _SECTION_NAME.finditer(rest)
                      if re.sub(r"[#*\s]", "", rest[:m.start()])), None)
            if not m:
                break
            start = m.start()
            while start > 0 and rest[start - 1] in "#* ":
                start -= 1
            out.append(rest[:start].rstrip())
            rest = rest[start:].lstrip()
        h = _HEADING_HEAD.match(rest)
        if h:
            out.extend([h.group(1).rstrip(), h.group(2)])
        else:
            out.append(rest)
    # An empty line before every heading (Lars 23.11.2026, "ACTION ITEMS1.").
    spaced = []
    for line in out:
        if _SECTION.match(line) and _SECTION_NAME.search(line) and spaced and spaced[-1].strip():
            spaced.append("")
        spaced.append(line)
    return "\n".join(spaced)


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_D_DOT = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(20\d\d)\b")
_D_ISO = re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b")
_D_MDY = re.compile(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)
_D_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+(20\d\d)\b", re.I)
_D_MY = re.compile(r"\b" + _MON + r"\s+(20\d\d)\b", re.I)
MAX_TREND_AGE_DAYS = 7


def line_dates(line):
    """Every date in a line. "July 2026" counts as the last day of July."""
    out = []

    def add(y, m, d):
        try:
            out.append(datetime.date(int(y), int(m), int(d)))
        except ValueError:
            pass
    for d, m, y in _D_DOT.findall(line):
        add(y, m, d)
    for y, m, d in _D_ISO.findall(line):
        add(y, m, d)
    for mon, d, y in _D_MDY.findall(line):
        add(y, _MONTHS[mon.lower()[:3]], d)
    for d, mon, y in _D_DMY.findall(line):
        add(y, _MONTHS[mon.lower()[:3]], d)
    rest = _D_DMY.sub(" ", _D_MDY.sub(" ", line))
    for mon, y in _D_MY.findall(rest):
        m = _MONTHS[mon.lower()[:3]]
        nxt = datetime.date(int(y) + (m == 12), m % 12 + 1, 1)
        out.append(nxt - datetime.timedelta(days=1))
    return out


def fact_violations(text, now=None, rules=None):
    """Lines that state a forbidden claim. [] for a clean briefing.

    `rules` are the checks of the fact sheet (read from FACTS_PATH when not
    given); `now` is the run date for the seven-day rule of TOP TRENDS.
    """
    sheet = _rules_from_sheet() if rules is None else rules
    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    today = now.date() if isinstance(now, datetime.datetime) else now
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
        figures = _FIGURE.findall(_ERC8004.sub(" ", _CITATION.sub(" ", clean)))
        if section == "THREATS" and _ERC8004.search(clean):
            rules.append("ERC-8004 is an interop partner, never a threat (decision 18.09.2026)")
        if _X402.search(clean) and figures and not (
                _CHAINALYSIS_138.search(clean) and _138M.search(clean) and _BASE.search(clean)):
            rules.append("x402 figure not from Chainalysis (138M on Base, 14.10.2026)")
        if _XRP.search(clean) and figures:
            rules.append("XRP Ledger figure (none allowed)")
        if any(_MEMBERSHIP.search(c) and (_ORG_PAIR.search(c) or re.search(r"\bmembers?\b|\bjoin", c, re.I))
               and not _primary_cited(c) for c in _SENTENCE.split(clean)):
            rules.append("Membership claim without primary source")
        if _chainalysis_line(clean):
            # the sheet's own figure is sourced; any other figure in the line is not
            figures = _FIGURE.findall(_138M.sub(" ", _ERC8004.sub(" ", _CITATION.sub(" ", clean))))
        if figures and not (_CITATION.search(line) and _DATE.search(line)):
            rules.append("Figure without source and date")
        if section == "TOP TRENDS" and not _chainalysis_line(clean):
            dates = line_dates(line)
            if dates and (today - max(dates)).days > MAX_TREND_AGE_DAYS:
                rules.append(f"Older than {MAX_TREND_AGE_DAYS} days, not a trend "
                             f"(latest date {max(dates):%d.%m.%Y})")
        for rule in sheet:
            if _rule_hits(rule, clean, section):
                rules.append(rule["why"])
        for r in rules:
            out.append({"line_no": n, "rule": r, "line": line.strip()})
    return out


def _primary_cited(line):
    """True if a cited host names an organisation the line mentions.

    "blog.cloudflare.com" is primary for a line naming Cloudflare;
    "agentry.news" is not primary for Cloudflare and Tools for Humanity.
    """
    words = {w.lower() for w in re.findall(r"\b[A-Z][\w-]{3,}", line)}
    for m in _CITATION.finditer(line):
        ref = next(g for g in m.groups() if g)
        host = re.sub(r"^https?://", "", ref.strip()).split("/")[0].lower()
        labels = set(re.split(r"[.-]", host)) - {"www", "com", "org", "net", "io", "blog", "news"}
        if labels & words:
            return True
    return False


def mark_violations(text, violations):
    """Prefix every offending line with the fact-check mark and its rules."""
    by_line = {}
    for v in violations:
        by_line.setdefault(v["line_no"], []).append(v["rule"])
    lines = text.splitlines()
    for n, rules in sorted(by_line.items(), reverse=True):
        mark = f"{MARK} ({'; '.join(rules)}):"
        if _SECTION.match(lines[n - 1]):
            lines.insert(n - 1, mark)  # a heading keeps its own line
        else:
            lines[n - 1] = f"{mark} {lines[n - 1]}"
    lines.append("")
    lines.append(f"{MARK}: {len(by_line)} line(s) above contradict agents/scout_facts.md "
                 "after one regeneration; treat them as unverified.")
    return "\n".join(lines)


def produce_briefing(findings, facts, generate=None):
    """Generate, check, regenerate once on a hit, mark what survives.

    Returns (briefing, status) with status one of "clean", "regenerated",
    "marked", "failed".
    """
    raw = generate or generate_briefing

    def generate(*a, **kw):
        out = raw(*a, **kw)
        if out.startswith("Briefing failed"):
            return out
        return drop_covered_actions(headings_on_own_line(out))

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
    # A briefing that still breaks the fact sheet after one regeneration is
    # never sent, hold or not (ops SCOUT-FIX-2311): until 23.11.2026 only the
    # hold file kept a marked briefing from Lars.
    if MARK in briefing:
        notify.send_telegram(
            f"Market Watch {now:%Y-%m-%d %H:%M} UTC: fact check marked lines after "
            f"regeneration; briefing not sent (ops SCOUT-FIX-2311), see logs/scout_{now:%Y%m%d_%H%M}.md",
            channel=notify.WORKLOG, art="pipeline-fehler")
        print("Marked, not sent")
        return
    if os.path.exists(HOLD_PATH):
        print(f"Held, not sent ({HOLD_PATH} exists)")
        return
    text = f"📈 Market Watch — MolTrust Scout Briefing, {now:%Y-%m-%d %H:%M} UTC\n\n{briefing}"
    if not notify.send_telegram(text, channel=notify.WORKLOG, chunk=True, art="scout-bericht"):
        print(f"Telegram not sent ({notify.letzter_grund() or 'failed'})")


if __name__ == "__main__":
    run()
