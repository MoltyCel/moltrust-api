"""ThreadWatch: the ambassador's health is a reply, read from "Run done: N replies".

From 10.10.2026 the probe reported "no success line in last 200KB of log"
while the ambassador ran every 30 minutes: it looked for four POST patterns
that never occur in its log. The probe now reads the run line, N > 0 counts
as a reply, N = 0 as a run without one (A1, 13.10.2026).
"""
import gzip
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("yaml")
pytest.importorskip("requests")

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
_saved_argv = sys.argv
sys.argv = ["threadwatch"]  # module runs argparse.parse_args() at import
try:
    import threadwatch as TW
finally:
    sys.argv = _saved_argv

NOW = datetime(2026, 10, 13, 14, 0, 0, tzinfo=timezone.utc)


def run_line(t, n):
    return (f"[{t.strftime('%Y-%m-%dT%H:%M:%S')}] INFO: Run done: {n} replies, "
            "0 rate-limited, 0 low-effort, 0 off-topic, 0 gate")


def config(path):
    return {"agent_health": {
        "agent_stale_hours": 24,
        "moltbook_logs": {"ambassador": str(path)},
        "reply_agents": ["ambassador"],
    }}


def flags(path):
    return TW.probe_moltbook_logs(config(path), NOW)["flags"]


def runs(start, hours, n=0):
    """One run line every 30 minutes from start for the given hours."""
    return [run_line(start + timedelta(minutes=30 * i), n) for i in range(hours * 2)]


def test_reply_in_rotated_log_gives_hours_since(tmp_path):
    # The situation of 13.10.: last reply 10.10. 11:30 in the rotated file,
    # since then only runs with 0 replies.
    log = tmp_path / "ambassador.log"
    log.write_text("\n".join(runs(NOW - timedelta(hours=58), 58)) + "\n")
    rotated = tmp_path / "ambassador.log-20261011"
    rotated.write_text(run_line(datetime(2026, 10, 10, 11, 30, 20), 1) + "\n")
    os.utime(rotated, (1, 1))
    assert flags(log) == ["ambassador: seit 74 Stunden kein Reply"]


def test_reply_in_gz_generation_is_found(tmp_path):
    log = tmp_path / "ambassador.log"
    log.write_text("\n".join(runs(NOW - timedelta(hours=10), 10)) + "\n")
    gz = tmp_path / "ambassador.log-20261004.gz"
    with gzip.open(gz, "wt") as f:
        f.write(run_line(NOW - timedelta(hours=30), 2) + "\n")
    assert flags(log) == ["ambassador: seit 30 Stunden kein Reply"]


def test_zero_replies_is_not_a_reply(tmp_path):
    # "0 replies" must not count, "10 replies" must.
    log = tmp_path / "ambassador.log"
    log.write_text("\n".join([run_line(NOW - timedelta(hours=40), 10)]
                             + runs(NOW - timedelta(hours=20), 20, n=0)) + "\n")
    assert flags(log) == ["ambassador: seit 40 Stunden kein Reply"]


def test_no_reply_anywhere_says_at_least(tmp_path):
    log = tmp_path / "ambassador.log"
    log.write_text("\n".join(runs(NOW - timedelta(hours=30), 30)) + "\n")
    assert flags(log) == ["ambassador: seit mindestens 30 Stunden kein Reply"]


def test_stopped_agent_is_told_apart_from_quiet_one(tmp_path):
    log = tmp_path / "ambassador.log"
    log.write_text(run_line(NOW - timedelta(hours=30), 1) + "\n")
    assert flags(log) == ["ambassador: seit 30 Stunden kein Lauf",
                          "ambassador: seit 30 Stunden kein Reply"]


def test_log_without_run_line(tmp_path):
    log = tmp_path / "ambassador.log"
    log.write_text("[2026-10-13T13:00:00] INFO: something else\n")
    assert flags(log) == ["ambassador: kein 'Run done' im Log, der Agent läuft nicht"]


def test_recent_reply_stays_silent(tmp_path):
    # Schweigeprobe: running every 30 minutes, a reply 3 hours ago.
    log = tmp_path / "ambassador.log"
    lines = runs(NOW - timedelta(hours=23), 23)
    lines[40] = run_line(NOW - timedelta(hours=3), 1)
    log.write_text("\n".join(lines) + "\n")
    assert flags(log) == []


def test_old_patterns_no_longer_decide(tmp_path):
    # The POST pattern that the old probe looked for counts for nothing here.
    log = tmp_path / "ambassador.log"
    log.write_text("\n".join(runs(NOW - timedelta(hours=30), 30)
                             + [f"[{NOW.strftime('%Y-%m-%dT%H:%M:%S')}] INFO: Posted to m/x"])
                   + "\n")
    assert flags(log) == ["ambassador: seit mindestens 30 Stunden kein Reply"]


def test_other_agents_keep_post_patterns(tmp_path):
    poster = tmp_path / "moltbook.log"
    poster.write_text(f"[{(NOW - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S')}] "
                      "INFO: POSTED to m/agenttrust\n")
    cfg = {"agent_health": {"agent_stale_hours": 24,
                            "moltbook_logs": {"moltbook_poster": str(poster)},
                            "reply_agents": ["ambassador"]}}
    assert TW.probe_moltbook_logs(cfg, NOW)["flags"] == []


# The poster keeps the POST patterns, and they too have to be found behind a
# rotation. On 25.10.2026 at 08:05 moltbook.log had been fresh since 04:00, the
# day's post came at 09:00, and the probe said "no success line in last 200KB
# of log" although the post of 24.10. 09:00 sat in moltbook.log-20261025.
POSTER_NOW = datetime(2026, 10, 25, 8, 5, 0, tzinfo=timezone.utc)


def poster_flags(log):
    cfg = {"agent_health": {"agent_stale_hours": 24,
                            "moltbook_logs": {"moltbook_poster": str(log)},
                            "reply_agents": ["ambassador"]}}
    return TW.probe_moltbook_logs(cfg, POSTER_NOW)["flags"]


def test_poster_success_in_rotated_copy_after_rotation(tmp_path):
    log = tmp_path / "moltbook.log"
    log.write_text("[2026-10-25T04:00:06] INFO: heartbeat\n")
    rotated = tmp_path / "moltbook.log-20261025"
    rotated.write_text("[2026-10-24T09:00:10] INFO: POSTED to m/agenttrust\n")
    os.utime(rotated, (1, 1))
    assert poster_flags(log) == []


def test_poster_success_in_gz_generation_still_ages(tmp_path):
    log = tmp_path / "moltbook.log"
    log.write_text("")
    gz = tmp_path / "moltbook.log-20261023.gz"
    with gzip.open(gz, "wt") as f:
        f.write("[2026-10-23T09:00:12] INFO: POSTED to m/agenttrust\n")
    assert poster_flags(log) == ["moltbook_poster: no success in 47h"]


def test_poster_newest_generation_wins(tmp_path):
    log = tmp_path / "moltbook.log"
    log.write_text("[2026-10-25T04:00:06] INFO: heartbeat\n")
    newer = tmp_path / "moltbook.log-20261025"
    newer.write_text("[2026-10-24T09:00:10] INFO: POSTED to m/agenttrust\n")
    older = tmp_path / "moltbook.log-20261023.gz"
    with gzip.open(older, "wt") as f:
        f.write("[2026-10-20T09:00:00] INFO: POSTED to m/agenttrust\n")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    assert poster_flags(log) == []


def test_poster_without_success_anywhere(tmp_path):
    log = tmp_path / "moltbook.log"
    log.write_text("[2026-10-25T04:00:06] INFO: heartbeat\n")
    (tmp_path / "moltbook.log-20261025").write_text("[2026-10-24T09:00:10] ERROR: 500\n")
    assert poster_flags(log) == ["moltbook_poster: no success line in log or its rotated copies"]
