"""scripts/secrets_value_scan.py finds a secret value where it does not belong (ops S7).

A planted value in a log, a tracked repo file and an archive is found; a public
value and the places meant to hold secrets stay silent; the output never carries
the value. The journal is skipped here: CI has none of ours.
"""
import gzip
import io
import os
import pathlib
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "secrets_value_scan.py"
SECRET = "sk_test_" + "Q7x" * 10           # looks like a key, belongs to nobody
ADDRESS = "0x" + "ab" * 20                 # public by name


def run(home: pathlib.Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, SCAN_HOME=str(home), SECRETS_FILE=str(home / ".moltrust_secrets"))
    return subprocess.run([sys.executable, str(SCRIPT), "--skip", "journal"],
                          capture_output=True, text=True, env=env, timeout=120)


def home_with_secrets(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    (h / ".moltrust_secrets").write_text(f"STRIPE_SECRET_KEY={SECRET}\nBASE_ADDR={ADDRESS}\n")
    return h


def test_clean_home_reports_zero(tmp_path):
    h = home_with_secrets(tmp_path)
    (h / "logs").mkdir()
    (h / "logs" / "app.log").write_text(f"paid to {ADDRESS}\n")   # public, not a finding
    (h / ".pypirc").write_text(f"[pypi]\npassword = {SECRET}\n")   # allowed holder
    r = run(h)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "0", r.stdout


def test_value_in_a_log_and_in_a_gzipped_log_is_found(tmp_path):
    h = home_with_secrets(tmp_path)
    (h / "logs").mkdir()
    (h / "logs" / "app.log").write_text(f"auth header Bearer {SECRET}\n")
    (h / "logs" / "old.log.gz").write_bytes(gzip.compress(f"key={SECRET}\n".encode()))
    out = run(h).stdout
    assert out.splitlines()[-1] == "2"
    assert "app.log" in out and "old.log.gz" in out
    assert SECRET not in out


def test_value_in_a_tracked_repo_file_is_found(tmp_path):
    h = home_with_secrets(tmp_path)
    repo = h / "somerepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "config.py").write_text(f'KEY = "{SECRET}"\n')
    (repo / "untracked.txt").write_text(SECRET)
    subprocess.run(["git", "-C", str(repo), "add", "config.py"], check=True)
    out = run(h).stdout
    assert "config.py" in out and "untracked.txt" not in out
    assert out.splitlines()[-1] == "1"


def test_value_inside_an_archive_is_found(tmp_path):
    h = home_with_secrets(tmp_path)
    data = f"export STRIPE_SECRET_KEY={SECRET}\n".encode()
    with tarfile.open(h / "backup.tar.gz", "w:gz") as t:
        info = tarfile.TarInfo("inner/.env")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    out = run(h).stdout
    assert "backup.tar.gz!inner/.env" in out and out.splitlines()[-1] == "1"
    assert SECRET not in out


def test_unreadable_secrets_file_is_unreadable_not_zero(tmp_path):
    h = tmp_path / "empty"
    h.mkdir()
    out = run(h).stdout
    assert out.startswith("UNREADABLE")
