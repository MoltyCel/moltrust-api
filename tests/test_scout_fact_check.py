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
- Chainalysis counts more than 138 million x402 transactions on Base (Chainalysis, 14.10.2026) [chainalysis.com].

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


def test_xrp_without_figure_is_allowed_but_no_xrp_figure_at_all():
    assert scout.fact_violations("- x402 now settles on the XRP Ledger as well.") == []
    # SCOUT-FIX-W: no XRP Ledger figure, even with its own source and date
    assert scout.fact_violations("- XRP Ledger: 13.4M transactions (XRPL, 12.11.2026) [xrpl.org].") != []


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


# --- SCOUT-FIX-W: briefing of 18.11.2026 18:00 UTC ------------------------
#
# logs/scout_20261118_1800.md on the server, header "Fact check: clean" under
# the SCOUT-FIX check. Lines quoted verbatim; the four error kinds Lars named
# on 18.11.2026 must each be red.

BAD_1118 = """## 1. TOP TRENDS

- **Big platforms are moving into agent identity.** Microsoft announced agent identity support in Windows and an AI Transparency Initiative (Nov 17) [news.microsoft.com]. Cloudflare and Tools for Humanity announced the Trusted Agent Delegation (TAD) coalition (Nov 5) [agentry.news/agent].
  - x402 tracker data as of Nov 15 showed 200,680,559 cumulative transactions across 12 chains [danielmcglynn.com].
  - Separately, XRP Ledger passed 13.4 million cumulative payments, as reported by the RippleX Engineering Head [analyticsinsight.net/xrp].

## 3. THREATS

- **ERC-8004:** it has registry deployments across Ethereum, Base, Arbitrum, Optimism, Polygon and others [news.bitcoin.com]. 8004scan.io counts 21,562 registered agents across EVM chains [news.bitcoin.com]. That is an on-chain identity and reputation competitor, and it overlaps with MolTrust's Base anchoring.
"""


def test_18_11_briefing_is_red_on_all_four_errors():
    rules = {v["rule"] for v in scout.fact_violations(BAD_1118)}
    assert "ERC-8004 is an interop partner, never a threat (decision 18.09.2026)" in rules
    assert "x402 figure not from Chainalysis (138M on Base, 14.10.2026)" in rules
    assert "XRP Ledger figure (none allowed)" in rules
    assert "Membership claim without primary source" in rules
    lines = {v["line"] for v in scout.fact_violations(BAD_1118)}
    assert len(lines) == 4  # each quoted line is caught


def test_erc8004_outside_threats_is_allowed():
    text = "## 2. OPPORTUNITIES\n- Interoperability with ERC-8004 registries [eips.ethereum.org].\n"
    assert scout.fact_violations(text) == []
    assert scout.fact_violations("## 3. THREATS\n- ERC-8004 registries.\n") != []


def test_x402_figure_only_from_chainalysis_138m():
    ok = "- x402: more than 138 million transactions on Base (Chainalysis, 14.10.2026) [chainalysis.com]."
    assert scout.fact_violations(ok) == []
    blog = "- x402: 195,706,113 transactions across 12 chains (Oct 30) [agenteconomy.to]."
    assert scout.fact_violations(blog) != []
    # plain dates and small counts are not x402 figures
    assert scout.fact_violations("- Brave enabled x402 payments on Nov 12 [brave.com].") == []


def test_membership_needs_the_organisations_own_source():
    blog = "- Cloudflare and Tools for Humanity launched the TAD coalition (Nov 5) [agentry.news]."
    own = "- Cloudflare and Tools for Humanity launched the TAD coalition (Nov 5) [blog.cloudflare.com]."
    assert scout.fact_violations(blog) != []
    assert scout.fact_violations(own) == []
    assert scout.fact_violations("- Track the TAD coalition for standards openings.") == []


def test_every_figure_needs_source_and_date():
    assert scout.fact_violations("- Plurai raised $13M seed [agentry.news].") != []
    assert scout.fact_violations("- Plurai raised $13M seed (Nov 11) [plurai.ai].") == []
    # numbers inside a cited URL are not figures
    assert scout.fact_violations("- World Bank and ITU flag the gap [biometricupdate.com/202611/x].") == []


def test_prompt_demands_primary_source_and_date():
    prompt = scout.build_prompt({}, "facts")
    assert "primary source and its date" in prompt
    assert "Never list it under THREATS" in prompt
    assert "No XRP Ledger figures at all" in prompt
    facts = scout.load_facts()
    assert "ERC-8004 is an interop partner" in facts and "138 million" in facts


def test_hold_file_keeps_briefing_from_lars(tmp_path, monkeypatch):
    import datetime
    sent = []
    monkeypatch.setattr(scout.notify, "send_telegram", lambda text, **kw: sent.append((text, kw)) or True)
    monkeypatch.setattr(scout, "HOLD_PATH", str(tmp_path / "scout_hold"))
    now = datetime.datetime(2026, 11, 19, 8, 0)
    (tmp_path / "scout_hold").write_text("")
    scout.melden(now, CLEAN)
    assert sent == []
    scout.melden(now, scout.mark_violations(BAD_1118, scout.fact_violations(BAD_1118)))
    assert len(sent) == 1 and sent[0][1]["art"] == "pipeline-fehler"
    assert "Cloudflare" not in sent[0][0]  # the briefing itself is not sent
    (tmp_path / "scout_hold").unlink()
    scout.melden(now, CLEAN)
    assert sent[-1][1]["art"] == "scout-bericht"
