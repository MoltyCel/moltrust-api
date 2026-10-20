"""What may not go out under the MolTrust agent on Moltbook.

The platform's terms, "Limitations of Use" (version of 2026-03-15,
https://www.moltbook.com/terms), prohibit: "use this Site in conjunction with
sending unauthorized advertising, marketing, spam or commercial sales
content;". We post under an agent that exists to represent a company, so the
line is: explain something, sell nothing.

A rule in the system prompt is a request the model honours most of the time.
These tests cover the part that does not depend on the model's mood.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "agents" / "moltbook_poster.py").read_text()

# The poster imports requests and reads env at module scope, so the two pieces
# under test are lifted out rather than importing the module.
_ns: dict = {"re": re}
exec(compile(re.search(r"BANNED_PATTERNS = \[.*?\n\]\n", SRC, re.S).group(0), "<b>", "exec"), _ns)
exec(compile(re.search(r"def content_violations.*?return found\n", SRC, re.S).group(0), "<f>", "exec"), _ns)
content_violations = _ns["content_violations"]


class TestMoney:
    def test_a_price_is_refused(self):
        assert content_violations("x", "It costs $0.05 per call")
        assert content_violations("x", "5 USDC for a credential")

    def test_the_word_free_is_refused(self):
        """'Free API' is the single most common way this drifts into an advert."""
        assert "the word 'free'" in content_violations("x", "Our free API does this")

    def test_credits_and_pricing_are_refused(self):
        assert content_violations("x", "You get 100 credits on signup")
        assert content_violations("Pricing that makes sense", "body")


class TestCallToAction:
    def test_imperatives_aimed_at_the_reader_are_refused(self):
        for phrase in ["Sign up today", "Get started here", "Try it yourself",
                       "Check it out", "Learn more", "Visit us", "Join us"]:
            assert content_violations("x", phrase), phrase

    def test_install_commands_are_refused(self):
        assert content_violations("x", "pip install moltrust-mcp-server")
        assert content_violations("x", "npm install @moltrust/x402")


class TestLinks:
    def test_urls_and_domains_are_refused(self):
        assert content_violations("x", "See https://api.moltrust.ch/guard")
        assert content_violations("x", "Documented at moltrust.ch")

    def test_a_domain_in_the_title_is_caught_too(self):
        """Title and body are checked together — putting the link in the
        headline is not a way around the rule."""
        assert content_violations("Read it at moltrust.ch", "clean body")


class TestWhatIsAllowed:
    def test_a_technical_post_passes(self):
        assert content_violations(
            "Replaying a proof and recomputing a leaf are different checks",
            "A Merkle proof is a statement about a leaf. Change the record it was "
            "derived from and the proof still reaches the root, because it was "
            "never about the record.",
        ) == []

    def test_naming_the_company_is_not_advertising(self):
        """The rule bans selling, not existing. A post may say who wrote it."""
        assert content_violations(
            "We rewrote a field on rows we had already anchored",
            "MolTrust anchors records on Base. Five of them stopped reproducing "
            "their leaves after a correction, and nothing noticed for five months.",
        ) == []

    def test_a_cwe_reference_is_not_a_domain(self):
        assert content_violations("x", "The check maps to CWE-78 and CWE-918") == []


class TestReporting:
    def test_every_broken_rule_is_named_once(self):
        found = content_violations("x", "Free trial at moltrust.ch for $5 — sign up")
        assert len(found) == len(set(found))
        assert len(found) >= 3

    def test_a_clean_post_reports_nothing(self):
        assert content_violations("A title", "A body about hashing.") == []
