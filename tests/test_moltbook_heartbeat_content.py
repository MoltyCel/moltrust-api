"""The heartbeat's canned content, held to the same rule as the poster's.

Until 2026-09-23 this service ran past the rule entirely: ten post templates
and twelve comment templates, 8 of the 12 comments carrying a domain or an
install command and 4 of them offering 175 API credits. Moltbook marked 91 of
the agent's last 100 comments as spam, all of them scored 0, and none was
answered. The templates are gone and the rule is now imported from the poster,
so these tests are what stops a URL from coming back into the pools unnoticed.

The suite needs no database and no network. It does import the heartbeat
module, which is deliberate: the import wiring to agents/moltbook_poster.py is
part of what is under test, and a copy-pasted second rule would pass a test
that only read the lists.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "moltbook_heartbeat", ROOT / "moltbook" / "heartbeat.py")
heartbeat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(heartbeat)


class TestTheRuleIsShared:
    def test_the_heartbeat_uses_the_posters_rule(self):
        """One list of prohibitions, not two.

        A second copy would answer these tests while the poster's copy moved on
        underneath it, and the copy that drifts is always the untested one.
        """
        from agents.moltbook_poster import content_violations
        assert heartbeat.content_violations is content_violations

    def test_no_second_list_of_patterns_in_the_heartbeat(self):
        src = (ROOT / "moltbook" / "heartbeat.py").read_text()
        assert "BANNED_PATTERNS = [" not in src


class TestEveryTemplateSatisfiesTheRule:
    def test_post_templates(self):
        for entry in heartbeat.POSTS:
            broken = heartbeat.content_violations(
                entry.get("title", ""), entry.get("content", ""))
            assert broken == [], f"{entry.get('title', '?')!r}: {broken}"

    def test_relevant_comment_templates(self):
        for text in heartbeat.COMMENTS_RELEVANT:
            assert heartbeat.content_violations("", text) == [], text

    def test_welcome_comment_templates(self):
        for text in heartbeat.WELCOME_COMMENTS:
            assert heartbeat.content_violations("", text) == [], text


class TestTheCheckWouldCatchARegression:
    """Empty pools pass every loop above, so these prove the loops have teeth.

    Each case is one of the four ways the deleted templates broke the rule.
    """

    def test_a_url_is_caught(self):
        assert heartbeat.content_violations("", "Register at moltrust.ch")
        assert heartbeat.content_violations("", "Docs: https://api.moltrust.ch")

    def test_free_credits_are_caught(self):
        assert heartbeat.content_violations(
            "", "Welcome! 175 free API credits on signup")

    def test_an_install_command_is_caught(self):
        assert heartbeat.content_violations("", "pip install moltrust-mcp-server")

    def test_a_call_to_action_is_caught(self):
        assert heartbeat.content_violations("", "Check it out")


class TestWhatIsSent:
    """The filtered pools are what the ticks read, and they hold only clean text."""

    def test_a_dirty_template_never_reaches_the_send_pool(self):
        kept = heartbeat.usable_comments([
            "A Merkle proof is a statement about a leaf, not about the record "
            "the leaf was derived from.",
            "Get a verified identity at moltrust.ch",
        ])
        assert len(kept) == 1
        assert "moltrust.ch" not in kept[0]

    def test_a_dirty_post_never_reaches_the_send_pool(self):
        assert heartbeat.usable_posts([
            {"title": "Agent onboarding in 30 seconds",
             "content": "Free tier available. Docs at moltrust.ch"},
        ]) == []

    def test_the_send_pools_are_clean(self):
        for text in heartbeat.COMMENT_POOL + heartbeat.WELCOME_POOL:
            assert heartbeat.content_violations("", text) == []
        for entry in heartbeat.POST_POOL:
            assert heartbeat.content_violations(
                entry.get("title", ""), entry.get("content", "")) == []
