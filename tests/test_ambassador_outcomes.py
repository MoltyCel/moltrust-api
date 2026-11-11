"""Every post a run finds ends in one outcome (ops AMB-NULL, 28.10.2026).

From 27.10. to 28.10.2026 forty-two runs logged "Found 10 posts: 8 our own,
0 foreign threads we have commented in, 2 foreign threads that mention us" and
then "Run done: 0 replies, 0 rate-limited, 0 low-effort, 0 off-topic, 0 gate".
Both mentions were already in seen_comments — one refused as i4 on 27.10., the
other deleted — and agents/ambassador.py:1397 passed over them without a line.

The reply path is exercised with every network call replaced; nothing is posted.
The check script is run against log lines in the form the cron log carries.
"""
import logging
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "agents"), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ambassador  # noqa: E402
import ambassador_outcome_check as check  # noqa: E402

MENTION_A = "50ebfec7-cca1-4758-aae0-eb4905d3076e"
MENTION_B = "0318d4a1-518b-4e54-bef6-ca009fcfa8b2"
QUESTION = ("Key rotation and delegated credentials: who revokes the inherited "
            "decision, and when does the old signature stop counting?")


class _Client:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def run(monkeypatch, tmp_path):
    """cmd_run over a stubbed surface; every outward call replaced."""
    monkeypatch.setattr(ambassador, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(ambassador, "DROPPED_FILE", tmp_path / "dropped.jsonl")
    monkeypatch.setattr(ambassador, "WS_LOGS", tmp_path / "wslogs")
    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        lambda state, key: (run.room, "stub", {"mode": "ok"}))
    monkeypatch.setattr(ambassador.comment_gate, "_our_comments", lambda key: [])
    monkeypatch.setattr(ambassador.comment_gate, "check_reply",
                        lambda *a, **k: (True, []))
    monkeypatch.setattr(ambassador.comment_gate, "worth_answering",
                        lambda text: (("trust" in text.lower() or "key" in text.lower()),
                                      "i4 Kein Thema — not about agent trust"))
    monkeypatch.setattr(ambassador.reply_radar, "load_kb", lambda: {})
    monkeypatch.setattr(ambassador, "read_surface", lambda c: [dict(p) for p in run.surface])
    monkeypatch.setattr(ambassador, "get_post", lambda c, pid: run.posts.get(pid, {}))
    monkeypatch.setattr(ambassador, "get_comments", lambda c, pid: list(run.threads.get(pid, [])))
    sent = []
    monkeypatch.setattr(ambassador, "moltbook_post",
                        lambda c, path, body: sent.append((path, body)) or {"id": "x"})
    monkeypatch.setattr(ambassador, "generate_reply",
                        lambda *a, **k: "A rotated key ends the inherited decision.")
    for name, value in {"check_agent_rate_limit": lambda n: False,
                        "check_reply_dedup": lambda n, t: None,
                        "is_identity_question": lambda t: False,
                        "get_stage": lambda s, n, t: 1,
                        "record_reply": lambda *a, **k: None,
                        "write_memory_entry": lambda **k: None,
                        "solve_verification": lambda *a, **k: None}.items():
        monkeypatch.setattr(ambassador, name, value)
    monkeypatch.setattr(ambassador.time, "sleep", lambda s: None)
    monkeypatch.setattr(ambassador.httpx, "Client", lambda *a, **k: _Client())
    monkeypatch.setattr(ambassador, "DRY_RUN", False)
    run.room = 2
    run.surface, run.posts, run.threads = [], {}, {}
    run.sent = sent
    run.state = {"seen_comments": {}, "replies_posted": 0}
    return run


def _outcomes(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Post ")]


def _done(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Run done:")][-1]


def _as_cron_log(caplog, start="2026-10-28T20:30:00"):
    """The records as the cron log carries them: '[ts] LEVEL: message'."""
    t0 = datetime.fromisoformat(start)
    lines = [f"[{t0:%Y-%m-%dT%H:%M:%S}] INFO: MOLTRUST AMBASSADOR — run\n"]
    for i, r in enumerate(caplog.records, start=1):
        ts = t0 + timedelta(seconds=i)
        lines.append(f"[{ts:%Y-%m-%dT%H:%M:%S}] {r.levelname}: {r.getMessage()}\n")
    return lines


def _the_28_october_surface(run):
    """Eight own posts, nothing new in them, and the two settled mentions."""
    run.surface = [{"id": f"own-{i}", "comment_count": 0 if i < 7 else 1,
                    "source": "own"} for i in range(8)]
    run.threads["own-7"] = [{"id": "c-old", "content": QUESTION,
                             "author": {"name": "vina"}, "author_id": "u-vina"}]
    run.state["seen_comments"]["own-7"] = ["c-old"]
    for pid in (MENTION_A, MENTION_B):
        run.surface.append({"id": pid, "comment_count": None, "source": "mention",
                            "mentions": [pid]})
        run.state["seen_comments"][pid] = [pid]


def test_the_28_october_run_now_names_every_post(run, caplog):
    _the_28_october_surface(run)
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert run.sent == []
    lines = _outcomes(caplog)
    assert len(lines) == 10, "one outcome line per post found"
    assert sum("unconsidered:already_seen" in line for line in lines) == 3
    assert sum("unconsidered:no_comments" in line for line in lines) == 7
    assert f"Post {MENTION_A[:8]} (mention): unconsidered:already_seen" in lines
    done = _done(caplog)
    assert "outcomes 10 of 10 posts:" in done
    assert "10 unconsidered (already_seen 3, no_comments 7)" in done


def test_the_check_fails_on_the_old_line_and_passes_on_the_new(run, caplog):
    now = datetime(2026, 10, 28, 20, 45, tzinfo=timezone.utc)
    old = ["[2026-10-28T20:30:03] INFO: MOLTRUST AMBASSADOR — run\n",
           "[2026-10-28T20:30:05] INFO: Found 10 posts: 8 our own, 0 foreign threads "
           "we have commented in, 2 foreign threads that mention us\n",
           "[2026-10-28T20:30:08] INFO: Run done: 0 replies, 0 rate-limited, "
           "0 low-effort, 0 off-topic, 0 gate\n"]
    assert check.silent_runs(old, now, 1) == (1, 1)

    _the_28_october_surface(run)
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert check.silent_runs(_as_cron_log(caplog), now, 1) == (0, 1)


def test_a_reply_settles_its_post_and_the_rest_stay_named(run, caplog):
    run.surface = [{"id": "own-1", "comment_count": 2, "source": "own"},
                   {"id": "own-2", "comment_count": 0, "source": "own"}]
    run.threads["own-1"] = [
        {"id": "c-q", "content": QUESTION, "author": {"name": "vina"}, "author_id": "u-v"},
        {"id": "c-off", "content": "Nice weather today", "author": {"name": "x"},
         "author_id": "u-x"}]
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert len(run.sent) == 1
    assert _outcomes(caplog) == ["Post own-1 (own): replied",
                                 "Post own-2 (own): unconsidered:no_comments"]
    assert "outcomes 2 of 2 posts: 1 replied, 0 skipped, 0 failed, 1 unconsidered" \
        in _done(caplog)


def test_an_off_topic_comment_is_skipped_not_unconsidered(run, caplog):
    run.surface = [{"id": "own-1", "comment_count": 1, "source": "own"}]
    run.threads["own-1"] = [{"id": "c-off", "content": "Nice weather today",
                             "author": {"name": "x"}, "author_id": "u-x"}]
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert _outcomes(caplog) == ["Post own-1 (own): skipped"]
    assert "1 off-topic" in _done(caplog)


def test_a_deleted_mention_names_its_reason(run, caplog):
    run.surface = [{"id": MENTION_B, "comment_count": None, "source": "mention",
                    "mentions": [MENTION_B]}]
    run.posts[MENTION_B] = {"id": MENTION_B, "title": "t", "content": "",
                            "is_deleted": True}
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert _outcomes(caplog) == [f"Post {MENTION_B[:8]} (mention): "
                                 "unconsidered:mention_unavailable"]


def test_a_mentioned_comment_that_is_not_in_the_thread(run, caplog):
    run.surface = [{"id": "foreign", "comment_count": None, "source": "mention",
                    "mentions": ["c-gone"]}]
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert _outcomes(caplog) == ["Post foreign (mention): "
                                 "unconsidered:mention_target_missing"]


def test_posts_after_a_spent_allowance_are_named_too(run, caplog):
    run.room = 1
    run.surface = [{"id": f"own-{i}", "comment_count": 1, "source": "own"}
                   for i in range(3)]
    for i in range(3):
        run.threads[f"own-{i}"] = [{"id": f"c-{i}", "content": QUESTION,
                                    "author": {"name": f"a{i}"}, "author_id": f"u{i}"}]
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert _outcomes(caplog) == ["Post own-0 (own): replied",
                                 "Post own-1 (own): unconsidered:out_of_room",
                                 "Post own-2 (own): unconsidered:out_of_room"]
    assert "outcomes 3 of 3 posts" in _done(caplog)


def test_our_own_comment_is_not_counted_as_a_skip(run, caplog):
    run.surface = [{"id": "own-1", "comment_count": 1, "source": "own"}]
    run.threads["own-1"] = [{"id": "c-ours", "content": "earlier",
                             "author": {"name": "moltrust-agent"}, "author_id": ""}]
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    assert _outcomes(caplog) == ["Post own-1 (own): unconsidered:ours_or_empty"]


def test_a_dry_run_drafts_nothing_and_writes_nothing(run, caplog, monkeypatch, tmp_path):
    monkeypatch.setattr(ambassador, "DRY_RUN", True)
    drafted = []
    monkeypatch.setattr(ambassador, "generate_reply",
                        lambda *a, **k: drafted.append(a) or "x")
    run.surface = [{"id": "own-1", "comment_count": 1, "source": "own"}]
    run.threads["own-1"] = [{"id": "c-q", "content": QUESTION,
                             "author": {"name": "vina"}, "author_id": "u-v"}]
    caplog.set_level(logging.INFO, logger="ambassador")
    ambassador.cmd_run(run.state)
    ambassador.write_log_entry("SKIP", "must not land")
    assert run.sent == [] and drafted == []
    assert not (tmp_path / "wslogs").exists(), "a dry run wrote the workspace log"
    assert _outcomes(caplog) == ["Post own-1 (own): would_reply"]
    assert "1 would_reply" in _done(caplog)


def test_dry_run_is_refused_for_anything_but_run(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ambassador.py", "post", "--dry-run"])
    with pytest.raises(SystemExit):
        ambassador.main()


# --- the check itself -------------------------------------------------------------

NOW = datetime(2026, 10, 28, 21, 0, tzinfo=timezone.utc)


def _run(ts, found, done):
    out = [f"[{ts}] INFO: MOLTRUST AMBASSADOR — run\n"]
    if found is not None:
        out.append(f"[{ts}] INFO: Found {found} posts: {found} our own\n")
    if done is not None:
        out.append(f"[{ts}] INFO: Run done: {done}\n")
    return out


def test_check_a_run_that_replied_is_not_silent():
    lines = _run("2026-10-28T20:30:00", 3, "1 replies, 0 rate-limited, 0 low-effort, "
                 "0 off-topic, 0 gate")
    assert check.silent_runs(lines, NOW, 1) == (0, 1)


def test_check_a_run_without_a_closing_line_is_silent():
    assert check.silent_runs(_run("2026-10-28T20:30:00", 3, None), NOW, 1) == (1, 1)


def test_check_parts_that_do_not_add_up_are_silent():
    done = ("0 replies, 0 rate-limited, 0 low-effort, 0 off-topic, 0 gate; "
            "outcomes 3 of 3 posts: 0 replied, 0 skipped, 0 failed, 2 unconsidered "
            "(already_seen 2)")
    assert check.silent_runs(_run("2026-10-28T20:30:00", 3, done), NOW, 1) == (1, 1)


def test_check_a_run_that_found_nothing_is_not_silent():
    assert check.silent_runs(_run("2026-10-28T20:30:00", None,
                                  "0 replies, 0 rate-limited, 0 low-effort, 0 off-topic, "
                                  "0 gate; outcomes 0 of 0 posts: 0 replied, 0 skipped, "
                                  "0 failed, 0 unconsidered"), NOW, 1) == (0, 1)


def test_check_an_empty_window_is_unreadable(tmp_path, capsys):
    log = tmp_path / "ambassador.log"
    log.write_text("".join(_run("2026-10-20T10:00:00", 3, None)))
    assert check.main(["--hours", "1", str(log)]) == 2
    assert capsys.readouterr().out.startswith("UNREADABLE")


def test_check_exit_code_follows_the_count(tmp_path, capsys):
    log = tmp_path / "ambassador.log"
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S")
    log.write_text("".join(_run(stamp, 10, "0 replies, 0 rate-limited, 0 low-effort, "
                                "0 off-topic, 0 gate")))
    assert check.main(["--hours", "1", str(log)]) == 1
    assert "ambassador-silent-run=1 runs=1" in capsys.readouterr().out
