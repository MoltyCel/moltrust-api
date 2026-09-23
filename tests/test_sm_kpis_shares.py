"""Share events, and the two test rows that must never reach a KPI."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("sm_kpis", ROOT / "scripts" / "sm_kpis.py")
sm_kpis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm_kpis)


class FakeRun:
    """Records the queries and answers them in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.queries = []

    def __call__(self, argv, **kw):
        self.queries.append(argv[argv.index("--query") + 1])

        class R:
            returncode = 0
            stdout = self.answers.pop(0)
            stderr = ""
        return R()


def test_the_two_test_events_are_excluded_by_timestamp(monkeypatch):
    fake = FakeRun(["share:x\t4\nshare:copy\t2\n", "/blog/a.html\t4\n"])
    monkeypatch.setattr(sm_kpis.subprocess, "run", fake)
    sm_kpis.share_events(7)

    for q in fake.queries:
        for stamp in sm_kpis.TEST_EVENTS:
            assert stamp in q, f"{stamp} is not excluded in: {q[:120]}"
        assert "share:copy" in q and "NOT (" in q


def test_channels_lose_the_prefix_and_are_totalled(monkeypatch):
    monkeypatch.setattr(sm_kpis.subprocess, "run",
                        FakeRun(["share:x\t4\nshare:bluesky\t1\n", "/blog/a.html\t3\n"]))
    out = sm_kpis.share_events(7)
    assert out["by_channel"] == {"x": 4, "bluesky": 1}
    assert out["total"] == 5
    assert out["top_post"] == {"path": "/blog/a.html", "count": 3}


def test_no_shares_is_zero_not_none(monkeypatch):
    monkeypatch.setattr(sm_kpis.subprocess, "run", FakeRun(["", ""]))
    out = sm_kpis.share_events(7)
    assert out["total"] == 0 and out["top_post"] is None


def test_a_broken_read_reports_nothing_rather_than_zero(monkeypatch):
    class Fail:
        def __call__(self, *a, **k):
            class R:
                returncode = 1
                stdout = ""
                stderr = "connection refused"
            return R()

    monkeypatch.setattr(sm_kpis.subprocess, "run", Fail())
    assert sm_kpis.share_events(7) is None
