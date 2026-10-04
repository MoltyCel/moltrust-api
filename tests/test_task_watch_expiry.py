"""Expiry is a state change and has to be said once.

TSK-E49N4V7T runs to 2026-10-05 20:51 UTC with seven of ten qualified. If it
ends short, the escrow needs a decision rather than an acceptance — and a
watcher that only alarms on success leaves that moment to somebody's memory.
"""
import datetime
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "task_watch", ROOT / "scripts" / "task_watch.py")
tw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tw)


def test_parse_iso_reads_the_market_format():
    d = tw.parse_iso("2026-10-05T20:51:11.355Z")
    assert d == datetime.datetime(2026, 10, 5, 20, 51, 11, 355000,
                                  tzinfo=datetime.timezone.utc)


def test_a_missing_or_broken_timestamp_is_not_a_time():
    # None must never compare as "expired". The whole point is that an
    # unreadable field produces no verdict.
    assert tw.parse_iso(None) is None
    assert tw.parse_iso("") is None
    assert tw.parse_iso("irgendwann") is None
    assert tw.parse_iso({"a": 1}) is None


def test_the_notice_fires_once_and_says_nothing_was_executed():
    src = (ROOT / "scripts" / "task_watch.py").read_text()
    assert "expiry_reported" in src
    # Once: guarded by the flag, and the flag is set in the same branch.
    assert src.index('if expired and not before.get("expiry_reported")') < \
        src.index('before["expiry_reported"] = True')
    assert "Nichts ausgefuehrt" in src
    assert "Rest-Escrow" in src
    # And it goes to ALERTS, not STATS — an expiry without acceptance is not a
    # statistic.
    tail = src.split("expiry_reported\")", 1)[1].split("= True", 1)[0]
    assert "notify.ALERTS" in tail


def test_nothing_in_this_file_accepts():
    """By the calls it makes, not by the words it uses.

    The first version of this test grepped for the string "accept" and tripped
    over two comments explaining that nothing is accepted here — the same
    text-instead-of-behaviour mistake this file's own invariants exist to catch.
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / "task_watch.py").read_text())
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name in ("cli", "run", "check_output", "check_call", "Popen"):
            for a in ast.walk(node):
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    calls.append(a.value)
    writing = [c for c in calls if any(
        w in c for w in ("accept", "reject", "cancel", "rate", "refund"))]
    assert not writing, f"dieser Qualifizierer ruft etwas Schreibendes: {writing}"
    assert set(c for c in calls if c in ("task", "get", "submissions",
                                         "download")) == {
        "task", "get", "submissions", "download"}
