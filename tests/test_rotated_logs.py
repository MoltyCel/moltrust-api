"""A log reader has to see past a rotation.

At 16:07 UTC on 23.10.2026 logrotate moved the logs outside the usual slot.
From 17:00 the watchdog reported "ReplyRadar: Job lief nicht" every hour until
the radar's next run at 06:05, and at 07:00 on 24.10 the daily report said
"Moltbook-Verifikation: keine Daten". Both agents had run as planned; the
readers only looked at the fresh, empty file.

The replay below uses the line shapes of the real logs from that day. Each
check has its silence probe too: a reader that sees past a rotation must still
go red when the rotated copy has nothing either.
"""
import datetime
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agents import watchdog as wd
from app import rotated_logs

ROOT = Path(__file__).resolve().parents[1]
CRONTAB = ("5 6 * * * cd /home/moltstack/moltstack && venv/bin/python "
           "agents/reply_radar.py >> logs/reply_radar.log 2>&1\n")


def _write(path: Path, lines, mtime: float, gz: bool = False) -> None:
    text = "\n".join(lines) + "\n"
    if gz:
        with gzip.open(path, "wt") as fh:
            fh.write(text)
    else:
        path.write_text(text)
    os.utime(path, (mtime, mtime))


def _at(stamp: str) -> float:
    return datetime.datetime.fromisoformat(stamp).replace(
        tzinfo=datetime.timezone.utc).timestamp()


# ── the helper ──

def test_tail_reaches_into_the_rotated_copy(tmp_path):
    log = tmp_path / "x.log"
    _write(tmp_path / "x.log-20261023", ["a", "b", "c"], _at("2026-10-23T16:07:07"))
    _write(log, ["d"], _at("2026-10-23T17:00:00"))
    assert rotated_logs.tail_lines(str(log), 3) == ["b", "c", "d"]


def test_tail_reads_compressed_and_numbered_copies_newest_first(tmp_path):
    log = tmp_path / "x.log"
    _write(tmp_path / "x.log-20261016.gz", ["old"], _at("2026-10-16T04:00:00"), gz=True)
    _write(tmp_path / "x.log.1", ["mid"], _at("2026-10-20T04:00:00"))
    _write(log, ["new"], _at("2026-10-23T17:00:00"))
    assert rotated_logs.tail_lines(str(log), 10) == ["old", "mid", "new"]


def test_tail_does_not_read_further_than_it_needs(tmp_path):
    log = tmp_path / "x.log"
    _write(tmp_path / "x.log-20261023", ["rotated"], _at("2026-10-23T16:07:07"))
    _write(log, ["1", "2"], _at("2026-10-23T17:00:00"))
    assert rotated_logs.tail_lines(str(log), 2) == ["1", "2"]


def test_other_files_with_the_same_prefix_are_not_copies(tmp_path):
    log = tmp_path / "x.log"
    _write(tmp_path / "x.log-notes", ["stray"], _at("2026-10-23T16:07:07"))
    _write(tmp_path / "x.log.bak", ["stray"], _at("2026-10-23T16:07:07"))
    _write(log, ["only"], _at("2026-10-23T17:00:00"))
    assert rotated_logs.tail_lines(str(log), 10) == ["only"]


def test_a_current_file_missing_after_rotation_is_not_a_missing_log(tmp_path):
    """logrotate without `create` leaves no file until the next write."""
    _write(tmp_path / "x.log-20261023", ["kept"], _at("2026-10-23T16:07:07"))
    assert rotated_logs.tail_lines(str(tmp_path / "x.log"), 5) == ["kept"]


def test_no_file_and_no_copy_is_still_an_error(tmp_path):
    with pytest.raises(OSError):
        rotated_logs.tail_lines(str(tmp_path / "x.log"), 5)
    with pytest.raises(OSError):
        rotated_logs.read_with_previous(str(tmp_path / "x.log"))


def test_read_with_previous_takes_only_the_newest_copy(tmp_path):
    log = tmp_path / "x.log"
    _write(tmp_path / "x.log-20261016.gz", ["older"], _at("2026-10-16T04:00:00"), gz=True)
    _write(tmp_path / "x.log-20261023", ["previous"], _at("2026-10-23T16:07:07"))
    _write(log, ["current"], _at("2026-10-24T09:00:00"))
    assert rotated_logs.read_with_previous(str(log)) == ["previous", "current"]


# ── the radar check, replayed against 23.10.2026 ──

ROTATED_RADAR = [
    "[2026-10-23T06:05:02] INFO: REPLY RADAR — 2026-10-23 06:05 UTC",
    "[2026-10-23T06:05:03] INFO: Candidates after filtering: 12",
    "[2026-10-23T06:05:03] INFO: reading list: 8 posts of 12 candidates sent",
    "[2026-10-23T16:05:01] INFO: No decisions waiting",
]
FRESH_RADAR = [
    "[2026-10-23T16:10:02] INFO: No decisions waiting",
    "[2026-10-23T16:15:02] INFO: No decisions waiting",
]


def _radar_at(monkeypatch, log: Path, stamp: str) -> dict:
    monkeypatch.setattr(wd._radar_tail, "__defaults__", (str(log), wd.RADAR_TAIL_LINES))
    now = datetime.datetime.fromisoformat(stamp).replace(tzinfo=datetime.timezone.utc)
    return wd.check_radar_deadman(now, crontab=CRONTAB)


def test_radar_after_the_rotation_of_23_10_is_green(tmp_path, monkeypatch):
    log = tmp_path / "reply_radar.log"
    _write(tmp_path / "reply_radar.log-20261023", ROTATED_RADAR, _at("2026-10-23T16:07:08"))
    _write(log, FRESH_RADAR, _at("2026-10-23T16:15:02"))
    r = _radar_at(monkeypatch, log, "2026-10-23T17:00:09")
    assert r["ok"], r["detail"]
    assert "letzter Entwurf vor 10.9 h" in r["detail"]


def test_radar_silence_probe_red_when_the_copy_has_no_run_either(tmp_path, monkeypatch):
    log = tmp_path / "reply_radar.log"
    _write(tmp_path / "reply_radar.log-20261023", FRESH_RADAR, _at("2026-10-23T16:07:08"))
    _write(log, FRESH_RADAR, _at("2026-10-23T16:15:02"))
    r = _radar_at(monkeypatch, log, "2026-10-23T17:00:09")
    assert not r["ok"]
    assert "Job lief nicht" in r["detail"]


def test_radar_silence_probe_red_when_the_last_delivery_is_too_old(tmp_path, monkeypatch):
    log = tmp_path / "reply_radar.log"
    _write(tmp_path / "reply_radar.log-20261023", ROTATED_RADAR, _at("2026-10-23T16:07:08"))
    _write(log, FRESH_RADAR, _at("2026-10-23T16:15:02"))
    r = _radar_at(monkeypatch, log, "2026-10-25T07:00:00")
    assert not r["ok"]


# ── the daily report's verification line ──

def _rate(log: Path) -> dict:
    p = subprocess.run([sys.executable, str(ROOT / "scripts/moltbook_verify_rate.py"),
                        "--log", str(log), "--json"], capture_output=True, text=True)
    return json.loads(p.stdout)


def test_verify_rate_counts_the_rotated_copy(tmp_path):
    log = tmp_path / "moltbook.log"
    _write(tmp_path / "moltbook.log-20261023", [
        "[2026-10-22T09:00:10] INFO: Verification passed! Answer: 42.00",
        "[2026-10-23T09:00:22] INFO: Verification passed! Answer: 60.00",
    ], _at("2026-10-23T16:07:07"))
    _write(log, [], _at("2026-10-23T16:07:07"))
    d = _rate(log)
    assert d["outcomes"] == 2 and d["passed"] == 2


def test_verify_rate_silence_probe_zero_when_nothing_verified(tmp_path):
    log = tmp_path / "moltbook.log"
    _write(tmp_path / "moltbook.log-20261023", ["[2026-10-23T09:00:02] INFO: start"],
           _at("2026-10-23T16:07:07"))
    _write(log, [], _at("2026-10-23T16:07:07"))
    assert _rate(log)["outcomes"] == 0


def test_daily_stats_keeps_the_json_of_a_small_sample():
    """Exit 1 (sample too small) must not turn the line into "keine Daten"."""
    src = (ROOT / "scripts/daily_stats.sh").read_text()
    assert "moltbook_verify_rate.py\" --json 2>/dev/null || echo" not in src
    assert "ambassador_dropped.py\" --html --hours 12" in src
