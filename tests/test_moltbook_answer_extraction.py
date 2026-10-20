"""The parser threw away answers the model got right.

Every string here is a real llm_raw from the Moltbook verify log, with the
answer the old parser produced and the one the model actually meant.
"""
from pathlib import Path

from lib.moltbook_verify import _format_answer


def test_the_2026_09_20_failure():
    """The model summed correctly and stated the result twice. The parser took
    the first operand and the post was rejected as an incorrect answer."""
    assert _format_answer("32.00 + 14.00 = 46.00\n\n46.00") == "46.00"


def test_a_bare_answer_still_works():
    assert _format_answer("30.00") == "30.00"
    assert _format_answer("  42  ") == "42.00"


def test_working_then_a_final_line():
    assert _format_answer("velocity = 32 + 14\n= 46\n\n46") == "46.00"
    assert _format_answer("The claw exerts 30 N and gains 12 N.\n42.00") == "42.00"


def test_a_truncated_self_correction_is_not_rescued():
    """Honest limit. When the model starts over and the response is cut off,
    there is no final answer to find and the old value is all there is. This
    test exists so nobody later reads the fix as covering that case."""
    assert _format_answer("32.00\n\nWait, let me recalculate. The problem states:\n-") == "32.00"


def test_trailing_punctuation_on_the_final_line():
    assert _format_answer("Working it out…\nThe answer is:\n46.00.") == "46.00"


def test_negatives_and_thousands_separators():
    assert _format_answer("-12.5") == "-12.50"
    assert _format_answer("total: 1,234.50") == "1234.50"


def test_nothing_to_find():
    assert _format_answer("") is None
    assert _format_answer("I cannot determine the answer.") is None


class TestSolverDiagnostics:
    """#372 asks which of two failure shapes is actually happening.

    It could not be answered from the log, and one of the two shapes turned
    out to be our own token limit rather than the model.
    """

    SRC = (Path(__file__).resolve().parents[1] / "lib" / "moltbook_verify.py").read_text()

    def test_the_token_limit_leaves_room_to_finish(self):
        """20 tokens cut a reasoning model mid-sentence.

        '32.00\\n\\nWait, let me recalculate…' in the log is that cut, not a
        model changing its mind, so the parser fix #372 proposed would have
        treated a truncation as a decision.
        """
        assert '"max_tokens": 20,' not in self.SRC
        assert '"max_tokens": 200,' in self.SRC

    def test_the_challenge_is_logged_whole(self):
        """Truncated at 120 characters, a model skipping a step and a
        malformed question look identical."""
        assert "text[:120]" not in self.SRC
        assert "challenge=%r llm_raw=%r answer=%s stop=%s" in self.SRC

    def test_the_stop_reason_is_recorded(self):
        """It distinguishes the two shapes outright: max_tokens means we cut
        the model off, end_turn means it finished and was wrong."""
        assert 'get("stop_reason")' in self.SRC
