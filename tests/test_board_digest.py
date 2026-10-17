"""Tests — der Lauf der Brett-Tageszeile (ops G5).

`tests/test_board.py` proves the text. This file proves the program around it:
what it sends, what it refuses to send, and what it does on a day with nothing
to say. Those are three different outcomes and only one of them produces a
post, so each is tested from the outside — `run()` in, heartbeat and send
attempts out.

The silence case is the one worth a test. An empty board is a declared quiet
state (`config/expectations.yaml`, `board_digest`), and a declared quiet state
that nobody checks is indistinguishable from a program that stopped working.
The run has to come back 0, write a heartbeat carrying the reason, and send
nothing at all — not to Moltbook, not to Telegram.

Nothing here opens a database or the network: `read_board` and the two send
paths are replaced, which is also the only way to test the day the content
rule refuses our own text.
"""
import datetime
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# Imported the way cron runs it, with agents/ on the path — that is what puts
# `moltbook_poster` within reach of the lazy import inside post_to_moltbook.
for p in (str(REPO), str(REPO / "agents")):
    if p not in sys.path:
        sys.path.insert(0, p)

import board_digest  # noqa: E402

from app import board  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 17, 11, 0, tzinfo=UTC)


def _entry(offers, did="did:moltrust:aaaaaaaaaaaaaaaa"):
    return {
        "did": did,
        "offers": list(offers),
        "seeks": ["group-task"],
        "note": "",
        "posted_at": NOW,
        "expires_at": board.expires_at(NOW),
    }


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """The run with its three outside edges held, and every attempt recorded."""
    # Heartbeat into the temporary tree, never the live one. paths.data() reads
    # this on every call, which is why it can be redirected at all.
    monkeypatch.setenv("MOLTRUST_ROOT", str(tmp_path))

    sent = {"moltbook": [], "telegram": []}

    def fake_post(title, body):
        sent["moltbook"].append((title, body))
        return "post-1"

    def fake_telegram(message, channel=None, **kw):
        sent["telegram"].append((channel, message))
        return True

    monkeypatch.setattr(board_digest, "post_to_moltbook", fake_post)
    monkeypatch.setattr(board_digest.notify, "send_telegram", fake_telegram)
    return sent


def _heartbeat(tmp_path):
    path = tmp_path / "data" / board_digest.HEARTBEAT_FILE
    return json.loads(path.read_text())


# --- A day with entries ----------------------------------------------------

def test_a_filled_board_posts_once_and_records_it(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(board_digest, "read_board",
                        lambda now: [_entry(["code"]), _entry(["data"], did="did:moltrust:b")])

    assert board_digest.run() == 0

    assert len(rig["moltbook"]) == 1
    title, body = rig["moltbook"][0]
    assert "2 open entries" in title
    assert "code" in body
    hb = _heartbeat(tmp_path)
    assert hb["status"] == "ok"
    assert "2 entries" in hb["detail"]
    # One line on the stats channel, not on alerts: a board with traffic is
    # the expected outcome and not an event anyone has to act on.
    assert [c for c, _ in rig["telegram"]] == [board_digest.notify.STATS]


def test_the_dry_run_builds_the_line_and_sends_nothing(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(board_digest, "read_board", lambda now: [_entry(["code"])])

    assert board_digest.run(dry_run=True) == 0

    assert rig["moltbook"] == []
    assert rig["telegram"] == []
    assert _heartbeat(tmp_path)["status"] == "ok"


# --- A day without entries: the declared quiet state -----------------------

def test_an_empty_board_posts_nothing_and_says_why(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(board_digest, "read_board", lambda now: [])

    assert board_digest.run() == 0

    # The silence proof. Nothing sent, anywhere.
    assert rig["moltbook"] == []
    assert rig["telegram"] == []
    hb = _heartbeat(tmp_path)
    assert hb["status"] == "ok"
    # The reason travels in the heartbeat, because a heartbeat that only says
    # "ok" cannot tell a quiet day from a broken one.
    assert "quiet" in hb["detail"]
    assert "empty" in hb["detail"]


def test_the_quiet_reason_is_the_one_the_register_declares(monkeypatch, tmp_path):
    """The log line and `config/expectations.yaml` have to carry one string.

    A declared quiet reason that the program does not print verbatim is a
    declaration about nothing: `agents/supervision.py` matches the log against
    the register, so a drift between the two reads as an undeclared silence.
    """
    import yaml

    register = yaml.safe_load((REPO / "config" / "expectations.yaml").read_text())
    block = next(p for p in register["pipelines"] if p["name"] == "board_digest")
    declared = block["silence_ok"]["brett_leer"]

    with pytest.raises(board.Rejected) as refused:
        board.digest_text([])
    # What the program logs is "board digest: nothing to post — <reason>".
    assert declared == f"nothing to post — {refused.value}"


# --- The day our own text does not pass ------------------------------------

def test_a_refused_text_is_an_error_and_not_a_quiet_day(rig, monkeypatch, tmp_path):
    """The content rule refusing the line is a defect, and it has to be loud.

    Treating it as silence would hide it exactly as long as the board stays
    busy — the one state in which the line matters.
    """
    monkeypatch.setattr(board_digest, "read_board", lambda now: [_entry(["code"])])
    monkeypatch.setattr(board_digest, "post_to_moltbook", lambda t, b: None)

    assert board_digest.run() == 1

    assert _heartbeat(tmp_path)["status"] == "error"
    assert [c for c, _ in rig["telegram"]] == [board_digest.notify.ALERTS]


def test_a_board_that_cannot_be_read_is_an_error(rig, monkeypatch, tmp_path):
    def explode(now):
        raise RuntimeError("no connection")

    monkeypatch.setattr(board_digest, "read_board", explode)

    assert board_digest.run() == 1

    assert rig["moltbook"] == []
    hb = _heartbeat(tmp_path)
    assert hb["status"] == "error"
    # The type, not the text: a connection string travels in that message.
    assert "RuntimeError" in hb["detail"]
    assert "no connection" not in hb["detail"]
    assert [c for c, _ in rig["telegram"]] == [board_digest.notify.ALERTS]


# --- Taskmarket ------------------------------------------------------------

def test_the_taskmarket_state_is_named_on_every_run(rig, monkeypatch, caplog):
    """Printed on the quiet day as well, because that is the day it reads as a gap.

    The job asks for the line on Moltbook *and* on taskmarket. Nothing is sent
    there (`agents/board_digest.TASKMARKET_BLOCKED`), so the reason is in the
    log of every run rather than in anyone's memory, and
    `config/expectations.yaml` declares the same string.
    """
    import yaml

    register = yaml.safe_load((REPO / "config" / "expectations.yaml").read_text())
    block = next(p for p in register["pipelines"] if p["name"] == "board_digest")
    assert block["silence_ok"]["taskmarket_ohne_kanal"] in board_digest.TASKMARKET_BLOCKED

    for entries in ([], [_entry(["code"])]):
        monkeypatch.setattr(board_digest, "read_board", lambda now, e=entries: e)
        with caplog.at_level("INFO", logger="board-digest"):
            caplog.clear()
            board_digest.run()
        assert any(board_digest.TASKMARKET_BLOCKED in r.message for r in caplog.records)
