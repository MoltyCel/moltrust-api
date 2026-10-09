"""moltbook/heartbeat.py imports when started the way its unit starts it.

moltbook-heartbeat.service runs `python heartbeat.py` with
WorkingDirectory=.../moltbook. From 2026-10-07 06:59 UTC every start failed
with ModuleNotFoundError: No module named 'app', 3,471 times. This imports the
module from that directory in a fresh interpreter, with the state root pointed
at a temporary directory, and without running the main loop.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_heartbeat_imports_from_the_moltbook_directory():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "logs"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MOLTRUST_ROOT": tmp,
               "HOME": tmp, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([sys.executable, "-c", "import heartbeat"],
                           cwd=ROOT / "moltbook", env=env, capture_output=True, text=True, timeout=60)
    assert "No module named 'app'" not in r.stderr
    assert r.returncode == 0, r.stderr[-2000:]
