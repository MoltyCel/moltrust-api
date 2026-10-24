"""The daily report's block on conversations the ambassador gave up on."""
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ambassador_dropped as ad  # noqa: E402

NOW = datetime.datetime(2026, 10, 24, 19, 0, tzinfo=datetime.timezone.utc)


def _file(tmp_path, *rows):
    p = tmp_path / "dropped.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return p


def _row(at, author="commenter"):
    return {"at": at, "comment_id": "6c75cae4-0f67", "author": author,
            "post_id": "db121107", "post_title": "One Agent Can Be Ten Agents If Nobody Is Counting",
            "refusal": "g2f Substanz-Boden — no concrete number anywhere",
            "fallback": "c9 Rückfall mit Zahl — the fallback carries a figure: 40"}


def test_none_is_said_rather_than_left_out(tmp_path, capsys):
    ad.main(["--file", str(tmp_path / "missing.jsonl")], now=NOW)
    assert capsys.readouterr().out.strip() == "Ambassador, aufgegebene Gespräche (12 h): keine"


def test_only_the_window_counts(tmp_path, capsys):
    f = _file(tmp_path, _row("2026-10-24T06:59:59Z", "old"), _row("2026-10-24T13:30:14Z"))
    ad.main(["--file", str(f)], now=NOW)
    out = capsys.readouterr().out
    assert "(12 h): 1" in out and "commenter" in out and "old" not in out
    assert "abgelehnt: g2f" in out and "Rückfall: c9" in out


def test_html_mode_escapes_for_telegram(tmp_path, capsys):
    r = _row("2026-10-24T13:30:14Z")
    r["post_title"] = "<b>A & B</b>"
    ad.main(["--file", str(_file(tmp_path, r)), "--html"], now=NOW)
    out = capsys.readouterr().out
    assert "&lt;b&gt;A &amp; B&lt;/b&gt;" in out and "<b>" not in out


def test_a_broken_line_does_not_stop_the_block(tmp_path, capsys):
    f = _file(tmp_path, _row("2026-10-24T13:30:14Z"))
    f.write_text("not json\n" + f.read_text())
    ad.main(["--file", str(f)], now=NOW)
    assert "(12 h): 1" in capsys.readouterr().out


def test_runs_under_the_system_python_without_the_repo_on_the_path(tmp_path):
    p = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/ambassador_dropped.py"),
                        "--file", str(tmp_path / "none.jsonl")], capture_output=True, text=True)
    assert p.returncode == 0 and "keine" in p.stdout
