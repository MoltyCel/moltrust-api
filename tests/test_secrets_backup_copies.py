"""secrets_backup.sh --check-copies: the scan behind c-secrets-no-plaintext-copy."""
import os
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "secrets_backup.sh")


def _scan(home):
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    out = subprocess.run(["bash", SCRIPT, "--check-copies"], env=env,
                         capture_output=True, text=True, check=True).stdout
    return out.strip().splitlines()


def test_finds_copies_and_counts_them(tmp_path):
    (tmp_path / ".moltrust_secrets").write_text("A=1\n")
    (tmp_path / ".moltrust_secrets.bak-2026-10-04").write_text("A=1\n")
    (tmp_path / "secret-backups").mkdir()
    (tmp_path / "secret-backups" / "moltrust_secrets.20260522T112910Z").write_text("A=1\n")
    (tmp_path / "cleanup-backups" / "20260510-0938").mkdir(parents=True)
    (tmp_path / "cleanup-backups" / "20260510-0938" / ".moltrust_secrets").write_text("A=1\n")
    lines = _scan(tmp_path)
    assert lines[-1] == "3"
    assert len(lines) == 4
    assert not any(ln.endswith("/.moltrust_secrets") and "cleanup-backups" not in ln
                   for ln in lines[:-1])


def test_original_and_encrypted_copies_are_silent(tmp_path):
    (tmp_path / ".moltrust_secrets").write_text("A=1\n")
    (tmp_path / "moltrust_secrets.20261011T093458Z.age").write_text("x")
    assert _scan(tmp_path) == ["0"]


def test_never_prints_content(tmp_path):
    (tmp_path / ".moltrust_secrets.bak-x").write_text("TOKEN=supersecretvalue\n")
    assert "supersecretvalue" not in "\n".join(_scan(tmp_path))
