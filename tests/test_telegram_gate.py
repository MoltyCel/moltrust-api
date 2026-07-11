"""_send_telegram_alert must only hit the network when MOLTRUST_ENV=production.

Proves test/local/CI runs never send: with a recording client injected, its
.post is never awaited outside production — even when the Telegram secrets are
present. A single positive case confirms the gate opens in production.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.budget import _send_telegram_alert


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


@pytest.mark.parametrize("env", [None, "", "test", "staging", "development", "PRODUCTIONX"])
async def test_never_sends_outside_production(monkeypatch, env):
    # Secrets present — so only the MOLTRUST_ENV gate can stop the send.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    if env is None:
        monkeypatch.delenv("MOLTRUST_ENV", raising=False)
    else:
        monkeypatch.setenv("MOLTRUST_ENV", env)

    client = _RecordingClient()
    await _call(client)
    assert client.calls == [], f"telegram send must not fire for MOLTRUST_ENV={env!r}"


async def test_sends_in_production(monkeypatch):
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")

    client = _RecordingClient()
    await _call(client)
    assert len(client.calls) == 1, "production must send exactly one telegram message"
    url, kwargs = client.calls[0]
    assert "api.telegram.org" in url and "/sendMessage" in url
    assert kwargs["json"]["chat_id"] == "test-chat"


async def test_production_but_missing_secrets_noops(monkeypatch):
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    client = _RecordingClient()
    await _call(client)
    assert client.calls == [], "missing secrets must no-op even in production"
