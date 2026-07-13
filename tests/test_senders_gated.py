"""Representative Telegram senders must be routed through notify.telegram_allowed().

Every standalone sender (agents/, scripts/, monitor/, workers/) was gated on the
shared MOLTRUST_NOTIFY flag via notify.telegram_allowed(). This proves the gate
for two representative implementations — one httpx-based (agents/watchdog.py) and
one requests-based (scripts/endpoint_probe.py): no network POST when MOLTRUST_NOTIFY
is unset, and exactly one POST when it is enabled. Secrets are present in both
cases, so only the gate — never missing creds — governs the outcome.
"""
import os
import sys

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "agents"))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

# endpoint_probe computes DRY_RUN = "--dry-run" in sys.argv at import — neutralise.
sys.argv = ["test_senders_gated"]

from app import notify  # noqa: E402
import watchdog as WD  # noqa: E402
import endpoint_probe as EP  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_gate(monkeypatch):
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", "/nonexistent")  # no secrets-file fallback
    notify._ENV_CACHE.clear()
    yield
    notify._ENV_CACHE.clear()


class _Resp:
    status_code = 200


def _recorder(calls):
    def _post(url, *args, **kwargs):
        calls.append((url, kwargs))
        return _Resp()
    return _post


# ── agents/watchdog.py — httpx-based, returns bool ────────────────────────────

def test_watchdog_no_send_when_notify_unset(monkeypatch):
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    monkeypatch.setattr(WD, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(WD, "TELEGRAM_CHAT_ID", "test-chat")
    calls = []
    monkeypatch.setattr(WD.httpx, "post", _recorder(calls))

    assert WD.send_telegram("hi") is False
    assert calls == [], "watchdog must not POST when MOLTRUST_NOTIFY is unset"


def test_watchdog_sends_when_notify_on(monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.setattr(WD, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(WD, "TELEGRAM_CHAT_ID", "test-chat")
    calls = []
    monkeypatch.setattr(WD.httpx, "post", _recorder(calls))

    assert WD.send_telegram("hi") is True
    assert len(calls) == 1, "watchdog must POST exactly once when notify enabled"
    assert "api.telegram.org" in calls[0][0]


# ── scripts/endpoint_probe.py — requests-based, returns None ──────────────────

def test_endpoint_probe_no_send_when_notify_unset(monkeypatch):
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    monkeypatch.setattr(EP, "TG_TOKEN", "test-token")
    monkeypatch.setattr(EP, "TG_CHAT_ID", "test-chat")
    calls = []
    monkeypatch.setattr(EP.requests, "post", _recorder(calls))

    EP.send_telegram("hi")
    assert calls == [], "endpoint_probe must not POST when MOLTRUST_NOTIFY is unset"


def test_endpoint_probe_sends_when_notify_on(monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.setattr(EP, "TG_TOKEN", "test-token")
    monkeypatch.setattr(EP, "TG_CHAT_ID", "test-chat")
    calls = []
    monkeypatch.setattr(EP.requests, "post", _recorder(calls))

    EP.send_telegram("hi")
    assert len(calls) == 1, "endpoint_probe must POST exactly once when notify enabled"
    assert "api.telegram.org" in calls[0][0]
