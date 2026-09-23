"""The Moltbook-spam block of the Sunday report.

The number gates the agent-to-agent offer, so the line has to survive the case
it is meant to produce: an identity that stopped commenting has nothing in the
window. Before 2026-09-23 that raised TypeError on a None percentage and took
the whole Sunday KPI message with it — the report broke exactly when the fix
it was watching had worked.

sm_kpis imports psycopg2, requests_oauthlib and app.notify at module scope, so
the function under test is lifted out of the file rather than imported, the way
tests/test_moltbook_content_rule.py does it.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "sm_kpis.py").read_text()

_ns: dict = {}
exec(compile(re.search(r"^SPAM_THRESHOLD_PCT = \d+$", SRC, re.M).group(0),
             "<c>", "exec"), _ns)
exec(compile(re.search(r"^def spam_lines.*?\n    return lines\n", SRC, re.M | re.S).group(0),
             "<f>", "exec"), _ns)
spam_lines = _ns["spam_lines"]
THRESHOLD = _ns["SPAM_THRESHOLD_PCT"]


def _row(**kw):
    base = {"comments": 100, "spam": 50, "pct": 50.0,
            "window_covered": True, "window_days": 7}
    base.update(kw)
    return base


class TestTheThresholdIsVisible:
    def test_the_heading_carries_it(self):
        assert f"{THRESHOLD} %" in spam_lines({})[0]

    def test_the_threshold_is_thirty(self):
        """Lars releases the agent-to-agent offer below this. Not decoration."""
        assert THRESHOLD == 30


class TestAnEmptyWindow:
    def test_no_comments_does_not_raise(self):
        lines = spam_lines({"u/x": _row(comments=0, spam=0, pct=None)})
        assert len(lines) == 2

    def test_no_comments_is_not_reported_as_zero_percent(self):
        """A share of nothing would read as a cleared gate."""
        line = spam_lines({"u/x": _row(comments=0, spam=0, pct=None)})[1]
        assert "%" not in line
        assert "keine Kommentare" in line


class TestWhatTheNumberIsCalled:
    def test_a_covered_window_is_reported_as_the_window(self):
        line = spam_lines({"u/x": _row(comments=362, spam=328, pct=90.6)})[1]
        assert "90.6 %" in line
        assert "328 von 362" in line
        assert "letzten 7 Tage" in line

    def test_a_short_read_is_called_a_sample_and_not_a_weekly_figure(self):
        line = spam_lines({"u/x": _row(comments=2000, spam=1800, pct=90.0,
                                       window_covered=False)})[1]
        assert "Stichprobe" in line
        assert "kein Wochenwert" in line

    def test_a_small_window_is_marked(self):
        line = spam_lines({"u/x": _row(comments=7, spam=0, pct=0.0)})[1]
        assert "Stichprobe klein" in line

    def test_a_full_window_is_not_marked_small(self):
        line = spam_lines({"u/x": _row(comments=20, spam=1, pct=5.0)})[1]
        assert "Stichprobe klein" not in line


class TestFailureIsNotAZero:
    def test_a_missing_key_is_named_rather_than_counted(self):
        line = spam_lines({"u/x": {"error": "MOLTBOOK_AGENT_KEY missing"}})[1]
        assert "nicht ermittelbar" in line
        assert "MOLTBOOK_AGENT_KEY missing" in line

    def test_nothing_measured_says_so(self):
        assert spam_lines({})[1].strip() == "nicht gemessen"


class TestBothIdentities:
    def test_each_identity_gets_its_own_line(self):
        lines = spam_lines({
            "u/moltrust-agent": _row(comments=362, spam=328, pct=90.6),
            "u/moltguard_v1": _row(comments=7, spam=0, pct=0.0),
        })
        assert len(lines) == 3
        assert "u/moltrust-agent" in lines[1]
        assert "u/moltguard_v1" in lines[2]
