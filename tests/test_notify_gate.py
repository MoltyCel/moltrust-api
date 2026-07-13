"""app/notify.py — the decoupled Telegram gate (MOLTRUST_NOTIFY, not MOLTRUST_ENV)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notify


@pytest.fixture(autouse=True)
def _clear_cache():
    notify._ENV_CACHE.clear()
    yield
    notify._ENV_CACHE.clear()


class _Recorder:
    def __init__(self):
        self.calls = []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        class _R:  # noqa
            status_code = 200
        return _R()


# --- the decoupling contract -------------------------------------------------
def test_gate_on_notify_flag(monkeypatch):
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", "/nonexistent")  # no fallback
    for v in ("on", "1", "true", "yes", "enabled", "production", "ON"):
        monkeypatch.setenv("MOLTRUST_NOTIFY", v)
        assert notify.telegram_allowed() is True, v
        notify._ENV_CACHE.clear()
    for v in ("", "off", "0", "false", "no", "ci", "staging"):
        monkeypatch.setenv("MOLTRUST_NOTIFY", v)
        assert notify.telegram_allowed() is False, v
        notify._ENV_CACHE.clear()


def test_decoupled_from_moltrust_env(monkeypatch):
    """MOLTRUST_ENV=production must NOT enable telegram; MOLTRUST_NOTIFY must, alone."""
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", "/nonexistent")
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    assert notify.telegram_allowed() is False  # ENV alone does not send
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.delenv("MOLTRUST_ENV", raising=False)
    assert notify.telegram_allowed() is True   # NOTIFY alone sends, no ENV needed


def test_secrets_file_fallback(monkeypatch, tmp_path):
    """dict-loader scripts (no os.environ) resolve via the secrets file."""
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    secrets = tmp_path / "secrets"
    secrets.write_text('OTHER=x\nMOLTRUST_NOTIFY="on"\n')
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", str(secrets))
    assert notify.telegram_allowed() is True
    notify._ENV_CACHE.clear()
    secrets.write_text("MOLTRUST_NOTIFY=off\n")
    assert notify.telegram_allowed() is False


def test_send_telegram_gated(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(notify.requests, "post", rec.post)
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", "/nonexistent")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    assert notify.send_telegram("x") is False and rec.calls == []
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    assert notify.send_telegram("x") is True and len(rec.calls) == 1
