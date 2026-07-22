"""_send_telegram_alert must only hit the network when MOLTRUST_NOTIFY is enabled.

Telegram is now gated by MOLTRUST_NOTIFY (decoupled from MOLTRUST_ENV, which
governs crypto posture). Proves test/local/CI runs never send even with secrets
present; a single positive case confirms the gate opens when notify is enabled.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notify
from app.budget import _send_telegram_alert


@pytest.fixture(autouse=True)
def _isolate_gate(monkeypatch):
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", "/nonexistent")  # no secrets-file fallback
    notify._ENV_CACHE.clear()
    yield
    notify._ENV_CACHE.clear()


class _RecordingClient:
    """Stand-in for httpx.AsyncClient that records calls instead of sending."""
    def __init__(self):
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return None

    async def aclose(self):
        pass


async def _call(client):
    await _send_telegram_alert(
        client, status="capped", operator_did="did:moltrust:" + "1" * 16,
        agent_did="did:moltrust:" + "2" * 16, spend=1.0, cap=1.0, pct=100.0)


@pytest.mark.parametrize("flag", [None, "", "off", "false", "0", "ci", "staging"])
async def test_never_sends_when_notify_disabled(monkeypatch, flag):
    # Secrets present — so only the MOLTRUST_NOTIFY gate can stop the send.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    if flag is None:
        monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    else:
        monkeypatch.setenv("MOLTRUST_NOTIFY", flag)

    client = _RecordingClient()
    await _call(client)
    assert client.calls == [], f"telegram send must not fire for MOLTRUST_NOTIFY={flag!r}"


async def test_moltrust_env_alone_does_not_send(monkeypatch):
    """Decoupling: MOLTRUST_ENV=production must NOT enable telegram (crypto flag only)."""
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")

    client = _RecordingClient()
    await _call(client)
    assert client.calls == [], "MOLTRUST_ENV must not control telegram sending"


async def test_sends_when_notify_enabled(monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.delenv("MOLTRUST_ENV", raising=False)  # no ENV needed
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    # Opt past the non-prod guard so the send path is actually exercised: clear
    # the pytest signal and pin DB_NAME to the production database.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("DB_NAME", "moltstack")

    client = _RecordingClient()
    await _call(client)
    assert len(client.calls) == 1, "notify-enabled must send exactly one telegram message"
    url, kwargs = client.calls[0]
    assert "api.telegram.org" in url and "/sendMessage" in url
    assert kwargs["json"]["chat_id"] == "test-chat"


async def test_notify_enabled_but_missing_secrets_noops(monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    client = _RecordingClient()
    await _call(client)
    assert client.calls == [], "missing secrets must no-op even when notify enabled"
