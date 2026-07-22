"""Non-prod guard on the budget Telegram alert.

Regression cover for the leak found on 2026-07-22: running the budget-cap suite
in a shell that had sourced the live secrets sent real alerts to the production
Telegram chat, because the send was gated only by MOLTRUST_NOTIFY/token and never
by the test-DB isolation. _send_telegram_alert must short-circuit before any HTTP
call when PYTEST_CURRENT_TEST is set (any pytest process) or DB_NAME points away
from the production database.
"""
from __future__ import annotations

import os

from app import budget


class _RecordingClient:
    """Stands in for httpx.AsyncClient; records any attempted send."""

    def __init__(self):
        self.calls = []

    async def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


async def test_no_send_under_pytest(monkeypatch):
    # pytest itself sets this for the duration of the test; assert we rely on a
    # real signal, not a contrived one.
    assert os.environ.get("PYTEST_CURRENT_TEST")

    # Make every OTHER gate pass, so only the non-prod guard can stop the send:
    # notify enabled, both secrets present, a working client supplied.
    monkeypatch.setenv("MOLTRUST_NOTIFY", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")

    client = _RecordingClient()
    await budget._send_telegram_alert(
        client,
        "capped",
        "did:moltrust:operatoraaaa0001",
        "did:moltrust:operatoraaaa0001",
        60.0,
        50.0,
        1.2,
    )
    assert client.calls == [], "alert must not be sent from a pytest process"


async def test_no_send_on_nonprod_db(monkeypatch):
    # Even outside pytest (signal cleared), a sandbox DB_NAME must suppress.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("DB_NAME", "moltstack_sandbox")
    monkeypatch.setenv("MOLTRUST_NOTIFY", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")

    client = _RecordingClient()
    await budget._send_telegram_alert(
        client, "warning", "did:x", "did:x", 40.0, 50.0, 0.8,
    )
    assert client.calls == [], "alert must not be sent against a non-prod DB"
