"""Crontab changes are logged with who, when and a copy before and after (ops KQ2).

The snapshot check names the last entry on a difference, and says when the live
crontab is not the one that entry left behind. Each rule has a case that fires
and one that stays quiet.
"""
import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import crontab_change as cc  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "check_crontab_snapshot_kq2", ROOT / "scripts" / "check_crontab_snapshot.py")
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)

NOW = datetime.datetime(2026, 10, 19, 12, 0, tzinfo=datetime.timezone.utc)
OLD = "*/5 * * * * python a.py\n"
NEW = "*/5 * * * * python a.py\n0 11 * * * python agents/board_digest.py\n"


class FakeCrontab:
    def __init__(self, text, list_rc=0, put_rc=0, sticks=True):
        self.text, self.list_rc, self.put_rc, self.sticks = text, list_rc, put_rc, sticks
        self.puts = []

    def __call__(self, cmd, input=None, **k):
        class R:
            pass
        r = R()
        if cmd == ["crontab", "-l"]:
            r.returncode, r.stdout = self.list_rc, self.text
        else:
            self.puts.append(input)
            r.returncode, r.stdout = self.put_rc, ""
            if self.put_rc == 0 and self.sticks:
                self.text = input
        return r


@pytest.fixture
def backups(monkeypatch, tmp_path):
    d = tmp_path / "crontab-backups"
    monkeypatch.setattr(cc, "BACKUPS", str(d))
    monkeypatch.setattr(snap.crontab_change, "BACKUPS", str(d))
    return d


def entries(d):
    return [json.loads(l) for l in (d / "changes.jsonl").read_text().splitlines()]


# ── the log ──

def test_a_change_leaves_both_copies_and_one_entry(backups):
    fake = FakeCrontab(OLD)
    rc, entry, why = cc.apply(NEW, "Koordinator", "CJ6: board line", run=fake, now=NOW)
    assert (rc, why) == (0, None)
    assert fake.puts == [NEW]
    [e] = entries(backups)
    assert e == entry
    assert (e["ts"], e["who"], e["why"], e["result"]) == (
        "2026-10-19T12:00:00+00:00", "Koordinator", "CJ6: board line", "ok")
    assert Path(e["before"]).read_text() == OLD and Path(e["after"]).read_text() == NEW
    assert e["before"].endswith("crontab-20261019T120000Z-before.txt")
    assert (e["after_sha256"], e["added"], e["removed"]) == (cc.sha256(NEW), 1, 0)


def test_every_writer_appends(backups):
    fake = FakeCrontab(OLD)
    cc.apply(NEW, "Koordinator", "CJ6", run=fake, now=NOW)
    cc.apply(OLD, "Console", "undo", run=fake, now=NOW + datetime.timedelta(minutes=1))
    assert [e["who"] for e in entries(backups)] == ["Koordinator", "Console"]


def test_a_note_records_without_writing(backups):
    fake = FakeCrontab(OLD)
    rc, e, _ = cc.apply(None, "Console", "baseline", run=fake, now=NOW)
    assert rc == 0 and fake.puts == []
    assert (e["result"], e["before_sha256"], e["after_sha256"]) == (
        "noted, unchanged", cc.sha256(OLD), cc.sha256(OLD))


@pytest.mark.parametrize("who,why", [("", "x"), ("Console", " ")])
def test_no_name_or_no_reason_changes_nothing(backups, who, why):
    fake = FakeCrontab(OLD)
    rc, e, msg = cc.apply(NEW, who, why, run=fake, now=NOW)
    assert (rc, e, fake.puts) == (2, None, [])
    assert "required" in msg and not backups.exists()


def test_an_unreadable_crontab_changes_nothing(backups):
    fake = FakeCrontab(OLD, list_rc=1)
    rc, e, msg = cc.apply(NEW, "Console", "x", run=fake, now=NOW)
    assert (rc, e, msg, fake.puts) == (2, None, "crontab -l exit 1", [])


def test_a_failed_install_is_logged_and_named(backups):
    rc, e, msg = cc.apply(NEW, "Console", "x", run=FakeCrontab(OLD, put_rc=1), now=NOW)
    assert (rc, msg, e["result"]) == (1, "crontab - exit 1", "crontab - exit 1")
    assert entries(backups) == [e]


def test_a_read_back_that_differs_is_named(backups):
    rc, e, msg = cc.apply(NEW, "Console", "x", run=FakeCrontab(OLD, sticks=False), now=NOW)
    assert (rc, msg) == (1, "read back differs from the candidate")


def test_the_command_line_needs_who_and_why(backups, monkeypatch, capsys):
    monkeypatch.setattr(cc.subprocess, "run", FakeCrontab(OLD))
    assert cc.main(["--who", "Console", "--why", "baseline", "--note"]) == 0
    assert "by Console: baseline (noted, unchanged" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cc.main(["--why", "baseline", "--note"])


# ── the snapshot check names it ──

def run_snapshot(monkeypatch, tmp_path, declared, live):
    f = tmp_path / "crontab.txt"
    f.write_text(declared)
    monkeypatch.setattr(snap, "SNAPSHOT", f)
    monkeypatch.setattr(snap.subprocess, "run", FakeCrontab(live))
    return snap.main()


def test_a_difference_names_the_last_entry_first(backups, monkeypatch, tmp_path, capsys):
    cc.apply(NEW, "Koordinator", "CJ6: board line", run=FakeCrontab(OLD), now=NOW)
    assert run_snapshot(monkeypatch, tmp_path, OLD, NEW) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("last logged change: 2026-10-19T12:00:00+00:00 by Koordinator: "
                             "CJ6: board line (ok, +1/-0; before ")
    assert out[1].startswith("RUNS, NOT DECLARED: 0 11 * * * python agents/board_digest.py")
    assert out[-1] == "1"
    assert not any("unlogged" in l for l in out)


def test_a_crontab_changed_after_the_last_entry_is_called_unlogged(
        backups, monkeypatch, tmp_path, capsys):
    cc.apply(None, "Console", "baseline", run=FakeCrontab(OLD), now=NOW)
    assert run_snapshot(monkeypatch, tmp_path, OLD, NEW) == 1
    out = capsys.readouterr().out.splitlines()
    assert "by Console: baseline" in out[0]
    assert out[1].startswith("live crontab is not the one the last logged change left behind")
    assert out[1].endswith("changed since, unlogged")


def test_without_a_log_the_finding_says_so(backups, monkeypatch, tmp_path, capsys):
    assert run_snapshot(monkeypatch, tmp_path, OLD, NEW) == 1
    assert capsys.readouterr().out.startswith("last logged change: none (")


def test_a_match_prints_only_the_count(backups, monkeypatch, tmp_path, capsys):
    cc.apply(None, "Console", "baseline", run=FakeCrontab(OLD), now=NOW)
    assert run_snapshot(monkeypatch, tmp_path, OLD, OLD) == 0
    assert capsys.readouterr().out == "0\n"
