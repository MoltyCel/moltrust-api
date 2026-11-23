"""Both probes for scripts/check_revoked_key_writes.py (invariant c-revoked-key-cannot-write)."""
from pathlib import Path

from scripts.check_revoked_key_writes import GUARD, GUARDED, findings, main

MAIN = Path(__file__).resolve().parent.parent / "app" / "main.py"


def _guarded_app(skip_guard_on: str | None = None, drop: str | None = None) -> str:
    parts = []
    for i, (method, path) in enumerate(GUARDED):
        if path == drop:
            continue
        body = "    pass" if path == skip_guard_on else f"    await {GUARD}(conn, did)"
        parts.append(f'@app.{method}("{path}")\nasync def h{i}(conn, did):\n{body}\n')
    return "\n".join(parts)


def test_silent_on_the_repo():
    assert findings(MAIN.read_text(encoding="utf-8")) == []


def test_silent_when_every_route_calls_the_guard():
    assert findings(_guarded_app()) == []


def test_fires_when_a_route_lost_the_guard():
    out = findings(_guarded_app(skip_guard_on="/board"))
    assert len(out) == 1 and "/board" in out[0] and "OHNE PRUEFUNG" in out[0]


def test_fires_when_a_route_is_gone():
    out = findings(_guarded_app(drop="/vc/ipr/submit"))
    assert len(out) == 1 and "/vc/ipr/submit" in out[0] and "FEHLT" in out[0]


def test_count_is_the_last_line(tmp_path, capsys):
    src = tmp_path / "main.py"
    src.write_text(_guarded_app(skip_guard_on="/vc/ipr/{ipr_id}/outcome"))
    assert main(["x", str(src)]) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == "1"
