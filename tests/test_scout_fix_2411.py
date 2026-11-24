"""Scout briefing of 24.11.2026: x401 and action items (ops SCOUT-FIX-2311).

Lars accepted facts and format of the 24.11. 08:00 briefing, with two
corrections: x401 signs transaction terms (amount, recipient) for the person,
so the split "x401 = who, AAE = what" is wrong; and the action items must not
repeat what a job or decision already covers. The lines below are quoted
verbatim from logs/scout_20261124_0800.md on the server.
"""
import datetime

from agents import scout

NOW = datetime.datetime(2026, 11, 24, 8, 0)

OPPORTUNITY_X401 = (
    "- Pre-transaction authorization is MolTrust's differentiator. Proof's x401 asks "
    "\"who is behind the agent and what was approved\". MolTrust's AAE (MANDATE / "
    "CONSTRAINTS / VALIDITY, as W3C Verifiable Credentials) covers the \"what is it "
    "allowed to do\" half.")

ACTIONS_1124 = """## 3. THREATS
- Hyperscaler-native agent payments (AWS AgentCore payments): payment and marketplace trust could be bundled into cloud platforms, which would reduce room for independent trust gates.

## 4. ACTION ITEMS
1. Write a short comparison of Proof x401 and the AAE, framed as identity versus authorization before the transaction.
2. Draft a one-page note on how @moltrust/x402 could sit in front of x402 endpoints, using the AWS AgentCore payments flow as the example.
3. Scope two possible additions and assign an owner to each: ANS interoperability (Linux Foundation, 10.11.2026) and a contribution to FG-TIDA.
4. Prepare ERC-8004 link-not-mint messaging for the next release notes.
"""


def _rules(text):
    return [v["rule"] for v in scout.fact_violations(text, now=NOW)]


def test_who_what_split_of_x401_and_aae_is_red():
    assert any("X401-VERGLEICH" in r for r in _rules(f"## 2. OPPORTUNITIES\n{OPPORTUNITY_X401}\n"))
    vs = "- x401 handles identity versus the AAE, which handles authorization."
    assert any("X401-VERGLEICH" in r for r in _rules(f"## 3. THREATS\n{vs}\n"))


def test_the_corrected_x401_line_is_green():
    line = ("- x401 and AAE overlap on authorization: x401 lets the person sign amount and "
            "recipient; comparison in progress (X401-VERGLEICH).")
    assert _rules(f"## 2. OPPORTUNITIES\n{line}\n") == []


def test_all_four_action_items_of_24_11_are_covered():
    out = scout.drop_covered_actions(ACTIONS_1124)
    section = out.split("## 4. ACTION ITEMS\n", 1)[1]
    assert section.strip() == scout.NO_NEW_ACTIONS
    assert "AWS AgentCore payments" in out  # THREATS untouched
    assert scout.fact_violations(out, now=NOW) == []


def test_covered_items_are_red_if_they_reach_the_check():
    rules = " | ".join(_rules(ACTIONS_1124))
    for job in ("X401-VERGLEICH", "AWS-X402 v2", "ITU-RESEND", "ERC-8004 decision 18.09.2026"):
        assert f"Already covered ({job})" in rules


def test_a_new_item_stays_and_is_renumbered():
    text = ("## 4. ACTION ITEMS\n1. Prepare ERC-8004 link-not-mint messaging.\n"
            "2. Answer the open question on the pricing page.\n"
            "   Owner: Lars.\n3. Write a comparison of Proof x401 and the AAE.\n")
    out = scout.drop_covered_actions(text)
    assert out == ("## 4. ACTION ITEMS\n1. Answer the open question on the pricing page.\n"
                   "   Owner: Lars.\n")


def test_covered_only_applies_to_action_items():
    line = "- AWS added x402 payments in AgentCore (AWS, 19.11.2026)."
    assert scout.drop_covered_actions(f"## 1. TOP TRENDS\n{line}\n") == f"## 1. TOP TRENDS\n{line}\n"
    assert _rules(f"## 1. TOP TRENDS\n{line}\n") == []


def test_produce_briefing_removes_covered_items():
    raw = "## 1. TOP TRENDS\n- News (Org, 23.11.2026).\n\n" + ACTIONS_1124
    briefing, status = scout.produce_briefing({}, "", generate=lambda *a, **kw: raw)
    assert status == "clean"
    assert briefing.rstrip().endswith(scout.NO_NEW_ACTIONS)


def test_sheet_has_x401_with_both_sources():
    with open(scout.FACTS_PATH, encoding="utf-8") as f:
        sheet = f.read()
    assert "helpnetsecurity.com/2026/06/26/proofs-x401" in sheet
    assert "thepaypers.com/fraud-and-fincrime/news/proof-launches-x401" in sheet
    rules = scout.sheet_rules(sheet)
    assert {r["job"] for r in rules if r["kind"] == "covered"} >= {
        "X401-VERGLEICH", "AWS-X402 v2", "ITU-RESEND", "KYA-VERGLEICH"}
    assert scout.self_test(rules) == len(rules)
