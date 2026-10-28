"""Every post a reply run finds ends with one outcome (ops AMB-NULL, 28.10.2026).

From 27.10. every run logged "Found 10 posts: 8 our own, 0 foreign threads we
have commented in, 2 foreign threads that mention us" and "Run done: 0 replies,
0 rate-limited, 0 low-effort, 0 off-topic, 0 gate". Measured live on 28.10.:
both mentions (50ebfec7 stealthmark, 0318d4a1 openclawprime) carried their own
post id in seen_comments, settled on 27.10. as i4 off topic and as a deleted
post, so every later run left them at `if target in seen`; the eight own posts
held only settled comments and left at `if cid in seen`. Neither counted.

The run here is the 28.10. surface rebuilt with every outward call stubbed.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "agents"), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ambassador  # noqa: E402
from agents import ambassador_outcomes as ao  # noqa: E402
from agents import watchdog as wd  # noqa: E402
from scripts import ambassador_outcome_check as check  # noqa: E402
from test_ambassador_mentions import run, FOREIGN  # noqa: E402,F401

STEALTHMARK = "50ebfec7-cca1-4758-aae0-eb4905d3076e"
OPENCLAWPRIME = "0318d4a1-518b-4e54-bef6-ca009fcfa8b2"
OWN = [f"own-{i}" for i in range(8)]
QUESTION = ("How does the registry keep a revoked delegation from being replayed "
            "by an agent that cached the old credential?")


def outcome_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Outcomes:")]


@pytest.fixture
def surface_of_28_10(run, monkeypatch):
    """Eight own posts with one settled comment each, two settled mentions."""
    posts = ([{"id": pid, "comment_count": 1, "source": "own",
               "title": "Own post", "content": "body"} for pid in OWN]
             + [{"id": m, "comment_count": None, "source": "mention",
                 "mentions": [m]} for m in (STEALTHMARK, OPENCLAWPRIME)])
    monkeypatch.setattr(ambassador, "read_surface", lambda c: [dict(p) for p in posts])
    threads = {pid: [{"id": f"c-{pid}", "content": QUESTION,
                      "author": {"name": "vina"}, "author_id": "u-vina"}]
               for pid in OWN}
    monkeypatch.setattr(ambassador, "get_comments", lambda c, pid: threads.get(pid, []))
    run.state["seen_comments"] = {pid: [f"c-{pid}"] for pid in OWN}
    run.state["seen_comments"].update({m: [m] for m in (STEALTHMARK, OPENCLAWPRIME)})
    run.threads = threads
    return run


def test_the_28_10_run_accounts_for_all_ten_posts(surface_of_28_10, caplog):
    caplog.set_level("INFO", logger="ambassador")
    code = ambassador.cmd_run(surface_of_28_10.state)
    assert surface_of_28_10.sent == [] and surface_of_28_10.drafts == []
    lines = outcome_lines(caplog)
    assert lines, "the run gave no outcome for the posts it found"
    assert lines[0].startswith("Outcomes: 10 posts found, 10 accounted for:")
    assert "10 not considered (10 already seen)" in lines[0]
    assert code == 0


def test_a_new_comment_is_answered_and_the_rest_counted(surface_of_28_10, caplog):
    caplog.set_level("INFO", logger="ambassador")
    surface_of_28_10.state["seen_comments"]["own-0"] = []
    assert ambassador.cmd_run(surface_of_28_10.state) == 0
    assert len(surface_of_28_10.sent) == 1
    line = outcome_lines(caplog)[0]
    assert "10 accounted for: 1 replied," in line
    assert "9 not considered (9 already seen)" in line


def test_a_deleted_mentioned_post_is_named_as_settled(run, monkeypatch, caplog):
    caplog.set_level("INFO", logger="ambassador")
    monkeypatch.setattr(ambassador, "get_post", lambda c, pid: {
        "id": pid, "title": "Deleted post", "content": "", "is_deleted": True})
    assert ambassador.cmd_run(run.state) == 0
    assert "1 not considered (1 mention settled)" in outcome_lines(caplog)[0]


def test_posts_past_the_allowance_are_counted_as_budget_exhausted(
        surface_of_28_10, monkeypatch, caplog):
    caplog.set_level("INFO", logger="ambassador")
    monkeypatch.setattr(ambassador.comment_gate, "run_allowance",
                        lambda state, key: (1, "1 this run (stub)", {"mode": "ok"}))
    for pid in OWN:
        surface_of_28_10.state["seen_comments"][pid] = []
    monkeypatch.setattr(ambassador, "check_agent_rate_limit", lambda n: False)
    # Distinct authors, so i5 does not settle the second one first.
    for i, pid in enumerate(OWN):
        surface_of_28_10.threads[pid][0]["author"] = {"name": f"agent-{i}"}
    assert ambassador.cmd_run(surface_of_28_10.state) == 0
    line = outcome_lines(caplog)[0]
    assert "10 accounted for: 1 replied," in line
    assert "budget exhausted" in line


def test_a_post_left_without_an_outcome_fails_the_run(surface_of_28_10, monkeypatch, caplog):
    """The invariant itself: lose one outcome and the run ends with exit 3."""
    real = ambassador.post_outcome
    calls = []

    def drops_the_first(marks):
        calls.append(marks)
        return real(marks) if len(calls) > 1 else ("lost", None)
    monkeypatch.setattr(ambassador, "post_outcome", drops_the_first)
    caplog.set_level("INFO", logger="ambassador")
    code = ambassador.cmd_run(surface_of_28_10.state)
    assert code == 3
    assert any("AMB-NULL invariant broken" in r.getMessage() for r in caplog.records)


def test_dry_run_drafts_nothing_and_names_what_it_would_answer(
        surface_of_28_10, monkeypatch, caplog):
    caplog.set_level("INFO", logger="ambassador")
    monkeypatch.setattr(ambassador, "DRY_RUN", True)
    surface_of_28_10.state["seen_comments"]["own-0"] = []
    assert ambassador.cmd_run(surface_of_28_10.state) == 0
    assert surface_of_28_10.sent == [] and surface_of_28_10.drafts == []
    assert "1 dry-run" in outcome_lines(caplog)[0]


# --- the log readers ------------------------------------------------------------

OLD_RUN = """\
[2026-10-28T19:30:02] INFO: MOLTRUST AMBASSADOR — run
[2026-10-28T19:30:02] INFO: === RUN: checking for new comments ===
[2026-10-28T19:30:05] INFO: Found 10 posts: 8 our own, 0 foreign threads we have commented in, 2 foreign threads that mention us
[2026-10-28T19:30:07] INFO: Run done: 0 replies, 0 rate-limited, 0 low-effort, 0 off-topic, 0 gate
[2026-10-28T19:30:07] INFO: Done.
"""


def new_run(at: datetime, replied=0, gate=0, settled=10):
    stamp = at.strftime("%Y-%m-%dT%H:%M:%S")
    found = replied + gate + settled
    return (f"[{stamp}] INFO: MOLTRUST AMBASSADOR — run\n"
            f"[{stamp}] INFO: Found {found} posts: 8 our own, 0 foreign threads "
            f"we have commented in, 2 foreign threads that mention us\n"
            f"[{stamp}] INFO: Outcomes: {found} posts found, {found} accounted for: "
            f"{replied} replied, 0 dry-run, 0 unsent, {gate} gate, 0 rate-limited, "
            f"0 low-effort, 0 off-topic, {settled} not considered "
            f"({settled} already seen)\n")


def test_the_check_fails_on_the_log_of_28_10(tmp_path, monkeypatch):
    log = tmp_path / "ambassador.log"
    log.write_text(OLD_RUN)
    monkeypatch.setattr(check, "datetime", _frozen(datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)))
    assert check.main(["--hours", "2", str(log)]) == 1


def test_the_check_passes_on_a_run_that_accounts(tmp_path, monkeypatch, capsys):
    now = datetime(2026, 10, 28, 21, 0, tzinfo=timezone.utc)
    log = tmp_path / "ambassador.log"
    log.write_text(new_run(now - timedelta(minutes=30)))
    monkeypatch.setattr(check, "datetime", _frozen(now))
    assert check.main(["--hours", "2", str(log)]) == 0
    assert "ambassador-unaccounted=0 runs=1" in capsys.readouterr().out


def test_the_check_without_a_run_is_unreadable(tmp_path, capsys):
    log = tmp_path / "ambassador.log"
    log.write_text("")
    assert check.main(["--hours", "2", str(log)]) == 2
    assert capsys.readouterr().out.startswith("UNREADABLE")


def _frozen(now):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    return Frozen


NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)


def day(**kw):
    return "".join(new_run(NOW - timedelta(minutes=30 * i), **kw)
                   for i in range(47, -1, -1)).splitlines(keepends=True)


def test_watchdog_turns_red_on_a_day_of_candidates_and_no_reply():
    r = wd.check_ambassador_answers(NOW, day(gate=1, settled=9))
    assert r["ok"] is False and not r.get("unverifiable")
    assert "48 Beiträge mit Antwort-Kandidaten, 0 Antworten" in r["detail"]


def test_watchdog_stays_green_when_one_reply_went_out():
    lines = day(gate=1, settled=9)
    lines += new_run(NOW - timedelta(minutes=5), replied=1, settled=9).splitlines(keepends=True)
    assert wd.check_ambassador_answers(NOW, lines)["ok"] is True


def test_watchdog_stays_green_when_nothing_was_worth_answering():
    assert wd.check_ambassador_answers(NOW, day())["ok"] is True


def test_watchdog_does_not_judge_before_a_full_day_of_outcome_lines():
    lines = OLD_RUN.splitlines(keepends=True) + new_run(NOW - timedelta(hours=2), gate=1).splitlines(keepends=True)
    r = wd.check_ambassador_answers(NOW, lines)
    assert r.get("unverifiable") is True


def test_the_reader_parses_what_cmd_run_writes():
    line = ambassador.outcome_line(10, {"not considered": 8, "gate": 2},
                                   {"already seen": 7, "mention settled": 1})
    stamp = "[2026-10-28T21:00:00] INFO: "
    runs = ao.runs([stamp + "MOLTRUST AMBASSADOR — run\n",
                    stamp + "Found 10 posts: x\n", stamp + line + "\n"])
    assert runs[0]["found"] == runs[0]["accounted"] == 10
    assert runs[0]["counts"]["gate"] == 2 and runs[0]["counts"]["not considered"] == 8
