"""ThreadWatch: is the ambassador stalled, read from its own log.

From 10.10.2026 the probe looked for POST patterns that never occur in the
ambassador's log; from 13.10. it read "Run done: N replies" and flagged
24 hours without a reply (A1). On 05.11. that said "seit 40 Stunden kein
Reply" while the agent ran every 30 minutes and nobody had commented
(AMB-STALL, 06.11.2026). Silence is not a stall. The probe now flags a run
that does not happen, and a comment the agent took up ("New comment by")
and did not settle. Each threshold has a test that fires and one that stays
silent.
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

NOW = datetime(2026, 11, 6, 18, 0, 0, tzinfo=timezone.utc)


def ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def run_line(t, n=0):
    return (f"[{ts(t)}] INFO: Run done: {n} replies, "
            "0 rate-limited, 0 low-effort, 0 off-topic, 0 gate; outcomes 24 of 24 posts")


def new_comment(t, author):
    return f"[{ts(t)}] INFO: New comment by {author} (stage 1) on 'On-Chain vs Off-Chain': text..."


def posted(t, author):
    return f"[{ts(t)}] INFO: Posted stage-1 reply to {author} on 'On-Chain vs Off-Chain'"


def config(path):
    return {"agent_health": {
        "agent_stale_hours": 24,
        "moltbook_logs": {"ambassador": str(path)},
        "reply_agents": ["ambassador"],
        "reply_watch": {"normal_reply_minutes": 30, "wait_hours": 6, "run_stale_hours": 3},
    }}


def probe(path, now=NOW):
    return TW.probe_moltbook_logs(config(path), now)


def runs(start, hours):
    """One run line every 30 minutes from start for the given hours."""
    return [run_line(start + timedelta(minutes=30 * i)) for i in range(hours * 2)]


def write(path, lines):
    path.write_text("\n".join(sorted(lines)) + "\n")


# --- silence: the case of 05.11.2026 ------------------------------------------

def test_quiet_agent_without_comments_stays_silent(tmp_path):
    # Schweigeprobe: 43 hours of runs, no comment, no reply (03.11. to 05.11.).
    log = tmp_path / "ambassador.log"
    write(log, runs(NOW - timedelta(hours=43), 43))
    out = probe(log)
    assert out["flags"] == [] and out["stalls"] == {}


def test_answered_comment_stays_silent(tmp_path):
    # Schweigeprobe: the round trip of 06.11., comment taken up and posted in one run.
    log = tmp_path / "ambassador.log"
    t = NOW - timedelta(hours=20)
    write(log, runs(NOW - timedelta(hours=23), 23)
          + [new_comment(t + timedelta(seconds=7), "chronosynth"),
             posted(t + timedelta(seconds=12), "chronosynth")])
    assert probe(log)["flags"] == []


def test_comment_settled_on_purpose_stays_silent(tmp_path):
    log = tmp_path / "ambassador.log"
    t = NOW - timedelta(hours=10)
    write(log, runs(NOW - timedelta(hours=12), 12)
          + [new_comment(t, "a"), f"[{ts(t + timedelta(seconds=5))}] INFO: Discarding the reply to a: rule",
             new_comment(t + timedelta(seconds=9), "b"),
             f"[{ts(t + timedelta(seconds=12))}] INFO: Gate blocked the reply to b after 2 drafts"])
    assert probe(log)["flags"] == []


def test_waiting_comment_just_under_threshold_stays_silent(tmp_path):
    log = tmp_path / "ambassador.log"
    write(log, runs(NOW - timedelta(hours=8), 8)
          + [new_comment(NOW - timedelta(hours=5, minutes=59), "x")])
    assert probe(log)["flags"] == []


# --- stall: a comment taken up and never answered ------------------------------

def test_failed_reply_fires_after_six_hours(tmp_path):
    log = tmp_path / "ambassador.log"
    t = NOW - timedelta(hours=7)
    write(log, runs(NOW - timedelta(hours=8), 8)
          + [new_comment(t, "chronosynth"),
             f"[{ts(t + timedelta(seconds=3))}] WARNING: Failed to generate reply for comment 7d35fe53"])
    out = probe(log)
    assert out["flags"] == ["ambassador: Kommentar von chronosynth seit 7 Stunden ohne Antwort"]
    assert out["stalls"]["ambassador"]["kind"] == "wait"


def test_reply_to_another_author_does_not_clear_the_wait(tmp_path):
    log = tmp_path / "ambassador.log"
    t = NOW - timedelta(hours=9)
    write(log, runs(NOW - timedelta(hours=10), 10)
          + [new_comment(t, "b"), new_comment(t + timedelta(seconds=4), "a"),
             posted(t + timedelta(seconds=9), "a")])
    assert probe(log)["flags"] == ["ambassador: Kommentar von b seit 9 Stunden ohne Antwort"]


def test_wait_found_behind_rotation(tmp_path):
    log = tmp_path / "ambassador.log"
    write(log, runs(NOW - timedelta(hours=10), 10))
    rotated = tmp_path / "ambassador.log-20261101"
    rotated.write_text(new_comment(NOW - timedelta(hours=30), "z") + "\n")
    os.utime(rotated, (1, 1))
    assert probe(log)["flags"] == ["ambassador: Kommentar von z seit 30 Stunden ohne Antwort"]


# --- stall: the agent does not run --------------------------------------------

def test_stopped_agent_fires_after_three_hours(tmp_path):
    log = tmp_path / "ambassador.log"
    write(log, runs(NOW - timedelta(hours=10), 6))  # last run 4.5 h ago
    out = probe(log)
    assert out["flags"] == ["ambassador: seit 4 Stunden kein Lauf"]
    assert out["stalls"]["ambassador"]["kind"] == "run"


def test_one_missed_run_stays_silent(tmp_path):
    log = tmp_path / "ambassador.log"
    write(log, runs(NOW - timedelta(hours=6), 5))  # last run 1.5 h ago
    assert probe(log)["flags"] == []


def test_log_without_run_line(tmp_path):
    log = tmp_path / "ambassador.log"
    log.write_text("[2026-11-06T13:00:00] INFO: something else\n")
    assert probe(log)["flags"] == ["ambassador: kein 'Run done' im Log, der Agent läuft nicht"]


# --- the alert: own message, plain words, once per stall ------------------------

def test_alert_text_is_plain_and_has_a_step():
    stall = {"kind": "wait", "hours": 7, "author": "chronosynth", "count": 1,
             "since": datetime(2026, 11, 6, 11, 0, tzinfo=timezone.utc)}
    text = TW.stall_alert("ambassador", stall, 30)
    assert text.startswith("Lars muss: ")
    assert "Handgriff:" in text and "chronosynth" in text and "06.11. 11:00 UTC" in text
    for word in ("log", "Log", "yaml", "Run done", "ambassador", "threadwatch", "stall", "cron"):
        assert word not in text, word


def test_alert_goes_once_per_stall_and_again_after_it_ends(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(TW.notify, "send_telegram",
                        lambda text, channel: sent.append((channel, text)) or True)
    log = tmp_path / "ambassador.log"
    write(log, runs(NOW - timedelta(hours=10), 6))
    state = {}
    p = probe(log)
    TW.send_stall_alerts([p], config(log), state)
    TW.send_stall_alerts([p], config(log), state)
    assert len(sent) == 1 and sent[0][0] == TW.notify.ALERTS
    TW.send_stall_alerts([{"stalls": {}}], config(log), state)   # over
    TW.send_stall_alerts([p], config(log), state)
    assert len(sent) == 2


def test_dry_run_does_not_use_up_the_alert(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(TW.notify, "send_telegram", lambda *a, **k: pytest.fail("sent in dry run"))
    log = tmp_path / "ambassador.log"
    write(log, runs(NOW - timedelta(hours=10), 6))
    state = {}
    TW.send_stall_alerts([probe(log)], config(log), state, dry=True)
    assert state["reply_stall_alerted"] == {} and "[dry-run ALERTS]" in capsys.readouterr().out


def test_other_agents_keep_post_patterns(tmp_path):
    poster = tmp_path / "moltbook.log"
    poster.write_text(f"[{ts(NOW - timedelta(hours=2))}] INFO: POSTED to m/agenttrust\n")
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
