"""Scout briefing: the fact sheet carries its own checks (ops SCOUT-FIX-2311).

Lars did not accept the briefings of 22.11. and 23.11.2026. The corrections
of 22.11. (FG-TIDA exists, ANS has a Linux Foundation primary source, Incode
is closed) never reached agents/scout_facts.md, and an entry there would only
have been prompt text: the check was code alone. Since SCOUT-FIX-2311 the
sheet has a section "Rules the check enforces" that fact_violations() applies,
and every run first tries each rule on its own example.

The lines below are quoted verbatim from logs/scout_20261123_1800.md on the
server (header "Fact check: regenerated", sent to Lars because the hold file
was gone).
"""
import datetime

import pytest

from agents import scout
from app import notify

NOW = datetime.datetime(2026, 11, 23, 18, 0)

BAD_1123 = """## 1. TOP TRENDS

- **Agent identity and trust is becoming a named product category.** Several announcements this month target "who is behind an agent":
  - GoDaddy contributed to Agent Name Service (ANS), an open standard for verifying who operates an agent and whether its software matches its registered version (Fortune, 19.11.2026).
  - Terminal 3 and Affinidi announced TENON, a public trust layer for verifying and governing agents (PR Newswire APAC, 20.11.2026).
- **Identity versus authorization is now an open policy question.** Biometric Update reports that the World Bank and ITU describe the gap between verifying individuals and verifying agent authorization as an emerging problem, and that ITU is forming a Focus Group. This matches MolTrust's core message: identity tells you who, authorization tells you what is allowed.
  - Klarna became the first non-card payment option inside Adyen Agentic (Klarna and Adyen, 20.11.2026).
- **ERC-8004 ecosystem keeps growing.** 0G Labs merged its registry deployment into the official ERC-8004 contracts repository, so agents on 0G are discoverable by ERC-8004-aware tools (0G Labs blog, July 2026).

## 3. THREATS

- **TENON (Terminal 3 and Affinidi):** a public trust layer with a governance angle, competing for the same "trust layer" positioning.
- **Incode Know Your Agent:** ties agent trust to verified human identity, a different anchor from MolTrust's DID and credential model.

## 4. ACTION ITEMS

1. Draft a short comparison of ANS, TENON and Incode Know Your Agent against `did:moltrust` and the AAE, focused on pre-transaction authorization.
"""

FIXED_1123 = """## 1. TOP TRENDS

- GoDaddy contributed to Agent Name Service (ANS), a Linux Foundation project (Linux Foundation, 10.11.2026; Fortune, 19.11.2026).
- AWS announced that agents can buy select AWS Marketplace products via x402 (AWS, 19.11.2026) [aws.amazon.com].
- Incode launched "Know Your Agent" (Incode, newsroom).

## 3. THREATS

- ANS (Linux Foundation, 10.11.2026) could become the default naming layer; the ITU Focus Group FG-TIDA (since 09.07.2026) works on agent identity.

## 4. ACTION ITEMS

1. Scope an ANS compatibility check (Linux Foundation, 10.11.2026).
"""


def _rules(text, now=NOW):
    return [v["rule"] for v in scout.fact_violations(text, now=now)]


def _lines(text, rule_start):
    return {v["line"] for v in scout.fact_violations(text, now=NOW) if v["rule"].startswith(rule_start)}


def test_every_error_of_23_11_is_red():
    rules = " | ".join(_rules(BAD_1123))
    assert "TENON has no primary source" in rules
    assert "Klarna in Adyen Agentic" in rules
    assert "Incode is closed" in rules
    assert "ANS is cited with its primary source" in rules
    assert "FG-TIDA exists since 09.07.2026" in rules
    assert "Older than 7 days, not a trend" in rules


def test_each_line_carries_its_rule():
    assert any("0G Labs" in ln for ln in _lines(BAD_1123, "Older than 7 days"))
    assert len(_lines(BAD_1123, "Incode is closed")) == 1  # the threat
    # the action item is covered by KYA-VERGLEICH (since 24.11.2026)
    assert len(_lines(BAD_1123, "Already covered (KYA-VERGLEICH)")) == 1
    assert all("TENON" in ln for ln in _lines(BAD_1123, "TENON"))


def test_corrected_briefing_is_green():
    assert scout.fact_violations(FIXED_1123, now=NOW) == []


def test_incode_in_trends_is_allowed_but_not_as_threat_or_action():
    assert _rules("## 1. TOP TRENDS\n- Incode launched Know Your Agent (Incode, newsroom).\n") == []
    assert _rules("## 3. THREATS\n- Incode Know Your Agent.\n") != []
    assert _rules("## 4. ACTION ITEMS\n- Compare Incode.\n") != []


def test_itu_focus_group_that_exists_is_allowed():
    assert _rules("## 1. TOP TRENDS\n- ITU's FG-TIDA, set up 09.07.2026, met again (ITU, 20.11.2026).\n") == []
    assert _rules("## 1. TOP TRENDS\n- ITU plans to set up a focus group.\n") != []


def test_age_rule_only_in_top_trends_and_not_for_the_sheet_figure():
    old = "- Affinidi Agent Gateway went live (Affinidi, 17.06.2026)."
    assert any(r.startswith("Older than") for r in _rules(f"## 1. TOP TRENDS\n{old}\n"))
    assert not any(r.startswith("Older than") for r in _rules(f"## 2. OPPORTUNITIES\n{old}\n"))
    chainalysis = ("- More than 138 million x402 transactions on Base "
                   "(Chainalysis, 14.10.2026, 2026 North America crypto-adoption report).")
    assert _rules(f"## 1. TOP TRENDS\n{chainalysis}\n") == []
    # seven days is still a trend, eight is not
    assert _rules("## 1. TOP TRENDS\n- News (Org, 16.11.2026).\n") == []
    assert _rules("## 1. TOP TRENDS\n- News (Org, 15.11.2026).\n") != []


def test_line_dates_reads_the_forms_the_briefings_use():
    d = datetime.date
    assert scout.line_dates("(Fortune, 19.11.2026)") == [d(2026, 11, 19)]
    assert scout.line_dates("(0G Labs blog, July 2026)") == [d(2026, 7, 31)]
    assert scout.line_dates("on November 10, 2026") == [d(2026, 11, 10)]
    assert scout.line_dates("10 November 2026") == [d(2026, 11, 10)]
    assert scout.line_dates("2026-11-10") == [d(2026, 11, 10)]
    assert scout.line_dates("no date here") == []


# --- the sheet's own rules and their self-test -----------------------------

def test_the_sheet_rules_all_fire_on_their_examples():
    with open(scout.FACTS_PATH, encoding="utf-8") as f:
        rules = scout.sheet_rules(f.read())
    assert len(rules) >= 5
    assert scout.self_test(rules) == len(rules)


def test_load_facts_runs_the_self_test(tmp_path):
    with open(scout.FACTS_PATH, encoding="utf-8") as f:
        sheet = f.read()
    broken = sheet.replace("match: \\bTENON\\b", "match: \\bTENNON\\b")
    assert broken != sheet
    p = tmp_path / "facts.md"
    p.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="does not fire on its own example"):
        scout.load_facts(str(p))


@pytest.mark.parametrize("line, msg", [
    ("- check: block | match: x | why: no example", "missing"),
    ("- check: banned | match: x | example: x | why: y", "unknown kind"),
    ("- check: not-in WEATHER | match: x | example: x | why: y", "unknown section"),
    ("- check: block | match: ( | example: x | why: y", "."),
])
def test_a_rule_that_cannot_be_read_stops_the_run(tmp_path, line, msg):
    with open(scout.FACTS_PATH, encoding="utf-8") as f:
        sheet = f.read()
    p = tmp_path / "facts.md"
    p.write_text(sheet + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(Exception, match=msg):
        scout.load_facts(str(p))


def test_sheet_names_the_primary_sources():
    with open(scout.FACTS_PATH, encoding="utf-8") as f:
        sheet = f.read()
    assert "https://www.linuxfoundation.org/press/linux-foundation-welcomes-agent-name-service" in sheet
    assert "PR-2026-07-09-focus-group-agentic-AI" in sheet
    assert "TENON" in sheet and "Adyen" in sheet


def test_the_rules_reach_the_prompt():
    assert "Rules the check enforces" in scout.load_facts()


# --- sending ----------------------------------------------------------------

def test_marked_briefing_is_never_sent_even_without_hold(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(scout.notify, "send_telegram", lambda text, **kw: sent.append((text, kw)) or True)
    monkeypatch.setattr(scout, "HOLD_PATH", str(tmp_path / "no_hold"))
    marked = scout.mark_violations(BAD_1123, scout.fact_violations(BAD_1123, now=NOW))
    scout.melden(NOW, marked)
    assert len(sent) == 1 and sent[0][1]["art"] == "pipeline-fehler"
    assert "TENON" not in sent[0][0]


def test_heading_gets_an_empty_line_before_it():
    out = scout.headings_on_own_line("- last trend.\n## 4. ACTION ITEMS\n1. Draft")
    assert out.split("\n") == ["- last trend.", "", "## 4. ACTION ITEMS", "1. Draft"]


def test_a_telegram_part_never_ends_on_a_heading():
    body = "\n".join(f"- line {i:03d} " + "x" * 60 for i in range(40))
    text = body + "\n\n## 4. ACTION ITEMS\n\n1. Draft a comparison.\n2. Next."
    limit = len(body) + 25  # the cut would fall right after the heading
    parts = notify._chunk(text, limit=limit)
    assert len(parts) == 2
    assert not parts[0].rstrip().endswith("ACTION ITEMS")
    assert parts[1].startswith("## 4. ACTION ITEMS")
    assert "\n".join(parts).replace("\n\n## 4", "\n## 4") .count("1. Draft") == 1
