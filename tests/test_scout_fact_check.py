"""Scout briefing: MolTrust facts come from agents/scout_facts.md (ops SCOUT-FIX).

The briefings of 17.11.2026 (logs/scout_20261117_0800.md and _1800.md on the
server) called Lightning payments, GitHub OAuth and did:web MolTrust
properties and reported XRP Ledger transaction counts as x402 scale. The
prompt had carried that self-description since the initial commit. These
tests hold the four errors red, a clean briefing green, and check that the
fact sheet is what the prompt carries.
"""
import pathlib
import re

from agents import scout

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Lines quoted verbatim from the two briefings of 17.11.2026.
BAD_0800 = """# MolTrust Scout Briefing

## 1. TOP TRENDS
- x402 standard scaling (Coinbase Agent.market: 69K agents, $50M volume by April 2026)

## 2. OPPORTUNITIES
✓ **GitHub OAuth → Production Verification**
✓ **Lightning Payments + Micropayments**
- x402 adoption accelerating; MolTrust's Lightning integration fills speed/cost gap vs. stablecoins
✓ **DID:web as Portable Identity**
- MolTrust as lightweight DID:web provider = onramp for interop frameworks
"""

BAD_1800 = """## 1. TOP TRENDS
- **Payment Infrastructure Live**: x402 protocol hitting scale—13.4M+ cumulative XRP Ledger transactions, 216K daily agentic transactions. Brave & OpenAI now enabling agent commerce natively.

## 2. OPPORTUNITIES
- **GitHub OAuth Integration Leverage**: Agent behavioral history (commits, activity) → verifiable reputation signals. Few competitors connecting this.
"""

CLEAN = """## 1. TOP TRENDS
- Linux Foundation took over stewardship of x402 [linuxfoundation.org].
- ANS launched with 11 early supporters [theregister.com]; it resolves agent names via did:web.
- XRP Ledger reports 13.4M cumulative transactions [xrpl.org].

## 2. OPPORTUNITIES
- MolTrust issues did:moltrust identities and accepts x402 payments in USDC on Base.
- Position AAE as the authorization layer ANS lacks. MolTrust has no Lightning rail and needs none.

## 3. THREATS
- Card networks building proprietary scoring.

## 4. ACTION ITEMS
- Contact the x402 maintainers about the offline trust gate.
"""


def _rules(text):
    return {v["rule"].split()[0] for v in scout.fact_violations(text)}


def test_17_11_briefings_are_red_on_all_four_errors():
    assert _rules(BAD_0800) >= {"Lightning", "GitHub", "did:web"}
    assert _rules(BAD_1800) >= {"XRP", "GitHub"}


def test_each_quoted_line_is_caught():
    hits = {v["line"] for v in scout.fact_violations(BAD_0800)}
    assert "✓ **Lightning Payments + Micropayments**" in hits
    assert "✓ **DID:web as Portable Identity**" in hits
    assert "- MolTrust as lightweight DID:web provider = onramp for interop frameworks" in hits


def test_clean_briefing_is_green():
    assert scout.fact_violations(CLEAN) == []


def test_negation_is_not_a_claim():
    assert scout.fact_violations("- MolTrust offers no Lightning payments.") == []
    assert scout.fact_violations("- MolTrust agents use did:moltrust, not did:web.") == []
    # and the same sentence without the negation is a claim
    assert scout.fact_violations("- MolTrust offers Lightning payments.") != []


def test_did_web_of_others_in_trends_is_allowed_but_not_in_opportunities():
    trend = "## 1. TOP TRENDS\n- ANS resolves names via did:web.\n"
    opp = "## 2. OPPORTUNITIES\n- did:web as portable identity.\n"
    assert scout.fact_violations(trend) == []
    assert scout.fact_violations(opp) != []


def test_xrp_without_x402_or_without_figure_is_allowed():
    assert scout.fact_violations("- XRP Ledger: 13.4M transactions [xrpl.org].") == []
    assert scout.fact_violations("- x402 now settles on the XRP Ledger as well.") == []


def _fake_generate(*answers):
    calls = []

    def gen(findings, facts, feedback=None):
        calls.append(feedback)
        return answers[len(calls) - 1]
    return gen, calls


def test_hit_regenerates_once_with_feedback():
    gen, calls = _fake_generate(BAD_0800, CLEAN)
    text, status = scout.produce_briefing({}, "facts", generate=gen)
    assert (text, status) == (CLEAN, "regenerated")
    assert calls[0] is None and calls[1]


def test_hit_after_regeneration_is_marked_not_sent_silently():
    gen, _ = _fake_generate(BAD_1800, BAD_1800)
    text, status = scout.produce_briefing({}, "facts", generate=gen)
    assert status == "marked"
    marked = [ln for ln in text.splitlines() if ln.startswith(scout.MARK)]
    assert len(marked) == 3  # two lines plus the footer
    assert scout.fact_violations(text) == []


def test_clean_first_draft_is_sent_unchanged():
    gen, calls = _fake_generate(CLEAN)
    assert scout.produce_briefing({}, "facts", generate=gen) == (CLEAN, "clean")
    assert len(calls) == 1


def test_prompt_carries_the_fact_sheet_and_not_the_old_sentence():
    facts = scout.load_facts()
    prompt = scout.build_prompt({"topic": ["finding"]}, facts)
    assert facts in prompt
    assert "Lightning payments, GitHub OAuth" not in prompt
    assert "W3C DID:web identity" not in prompt
    assert "did:moltrust" in facts and "x402" in facts and "USDC" in facts
    # maintainer text above the first section stays out of the prompt
    assert "Change a fact here only together with its source" not in facts


def test_prompt_demands_a_source_per_figure_and_forbids_cross_attribution():
    prompt = scout.build_prompt({}, "facts")
    assert "names its source from the" in prompt
    assert "Never attribute one project's figure to another" in prompt


def test_every_cited_line_exists_on_this_tree():
    """A fact whose source line is gone is a fact without a source."""
    text = (ROOT / "agents" / "scout_facts.md").read_text()
    cites = re.findall(r"(?<![\w-])((?:[\w.-]+/)*[\w.-]+\.(?:md|py|json)):(\d+)(?:-(\d+))?", text)
    assert cites
    for path, start, end in cites:
        if path == "index.html" or path.startswith("blog/"):
            continue  # moltrust-web
        f = ROOT / path
        assert f.is_file(), path
        n = len(f.read_text().splitlines())
        assert int(end or start) <= n, f"{path}:{start}"


def test_missing_fact_sheet_raises(tmp_path):
    import pytest
    with pytest.raises(OSError):
        scout.load_facts(tmp_path / "nope.md")
    p = tmp_path / "s.md"
    p.write_text("no section here\n")
    with pytest.raises(ValueError):
        scout.load_facts(p)
