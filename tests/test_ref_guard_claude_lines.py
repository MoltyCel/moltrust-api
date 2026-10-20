import datetime as dt
import json

from scripts import claude_procs, ref_guard

NOW = dt.datetime(2026, 10, 19, 8, 0, tzinfo=dt.timezone.utc)


def test_ref_guard_names_date_path_and_refusals(tmp_path):
    p = tmp_path / "rg.jsonl"
    rows = [{"ts": "2026-10-19T07:10:00+00:00", "mode": "refuse", "cwd": "/home/moltstack/moltstack",
             "refs": "refs/remotes/origin/main", "action": "pull -q"},
            {"ts": "2026-10-17T07:10:00+00:00", "mode": "log", "cwd": "/x", "refs": "HEAD", "action": "old"}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = ref_guard.lines(NOW, str(p))
    assert out[0].startswith("Ref-Wache") and "1 Eingriff(e), 1 abgelehnt" in out[0]
    assert "19.10. 07:10Z refuse /home/moltstack/moltstack" in out[1]
    assert len(out) == 2


def test_ref_guard_quiet_day(tmp_path):
    assert ref_guard.lines(NOW, str(tmp_path / "none.jsonl")) == [
        "Ref-Wache (24 h, ab 18.10. ablehnend): 0 Eingriff(e)"]


def _proc(tmp_path, pid, argv, comm, cwd):
    d = tmp_path / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "comm").write_text(comm + "\n")
    (d / "cwd").symlink_to(cwd)
    (d / "stat").write_text(f"{pid} ({comm}) S " + " ".join(["0"] * 17) + " 100 0\n")


def test_claude_processes_counted_others_not(tmp_path):
    (tmp_path / "stat").write_text("btime 1790000000\n")
    _proc(tmp_path, 10, ["claude"], "claude", "/home/moltstack/moltstack")
    _proc(tmp_path, 11, ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], "node", "/tmp")
    _proc(tmp_path, 12, ["grep", "claude"], "grep", "/tmp")             # a search for it is not it
    _proc(tmp_path, 13, ["python3", "agents/reply_radar.py"], "python3", "/tmp")
    ps = claude_procs.processes(str(tmp_path))
    assert [p[0] for p in ps] == [10, 11]
    out = claude_procs.lines(str(tmp_path))
    assert out[0].startswith("Claude-Prozesse auf dem Server: 2")
    assert "in /home/moltstack/moltstack" in out[1]
