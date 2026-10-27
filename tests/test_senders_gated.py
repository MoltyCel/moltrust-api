"""Representative Telegram senders must be routed through notify.telegram_allowed().

Every standalone sender (agents/, scripts/, monitor/, workers/) was gated on the
shared MOLTRUST_NOTIFY flag via notify.telegram_allowed(). This proves the gate
for two representative senders, agents/watchdog.py and scripts/endpoint_probe.py:
no network POST when MOLTRUST_NOTIFY is unset, and exactly one POST when it is
enabled. Secrets are present in both cases, so only the gate — never missing
creds — governs the outcome. Since ops NOTIFY-2 (27.10.2026) both send through
app/notify.py, so the POST is recorded at notify's requests.post.
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
def _isolate_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("MOLTRUST_SECRETS_FILE", "/nonexistent")  # no secrets-file fallback
    # Both senders go through app/notify.py since ops NOTIFY-2. Its throttle
    # and send log get a private directory, and the token comes from the
    # environment as in production after `set -a; . ~/.moltrust_secrets`.
    monkeypatch.setattr(notify, "SENT_LOG", str(tmp_path / "sent.jsonl"))
    monkeypatch.setattr(notify, "FINGERPRINTS", str(tmp_path / "fp.json"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    notify._ENV_CACHE.clear()
    yield
    notify._ENV_CACHE.clear()


class _Resp:
    status_code = 200
    content = b""

    def json(self):
        return {"result": {"message_id": 1}}


def _recorder(calls):
    def _post(url, *args, **kwargs):
        calls.append((url, kwargs))
        return _Resp()
    return _post


# ── agents/watchdog.py — returns bool ─────────────────────────────────────────

def test_watchdog_no_send_when_notify_unset(monkeypatch):
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    # Die Chat-ID kommt aus notify, nicht mehr aus einer Modulkonstante.
    monkeypatch.setattr(WD.notify, "chat_id_for", lambda ch: "test-chat")
    calls = []
    monkeypatch.setattr(notify.requests, "post", _recorder(calls))

    assert WD.send_telegram("hi") is False
    assert calls == [], "watchdog must not POST when MOLTRUST_NOTIFY is unset"


def test_watchdog_sends_when_notify_on(monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.setattr(WD.notify, "chat_id_for", lambda ch: "test-chat")
    calls = []
    monkeypatch.setattr(notify.requests, "post", _recorder(calls))

    assert WD.send_telegram("hi") is True
    assert len(calls) == 1, "watchdog must POST exactly once when notify enabled"
    assert "api.telegram.org" in calls[0][0]


# ── scripts/endpoint_probe.py — returns None ──────────────────────────────────

def test_endpoint_probe_no_send_when_notify_unset(monkeypatch):
    monkeypatch.delenv("MOLTRUST_NOTIFY", raising=False)
    monkeypatch.setattr(EP, "DRY_RUN", False)
    monkeypatch.setattr(EP.notify, "chat_id_for", lambda ch: "test-chat")
    calls = []
    monkeypatch.setattr(notify.requests, "post", _recorder(calls))

    EP.send_telegram("hi")
    assert calls == [], "endpoint_probe must not POST when MOLTRUST_NOTIFY is unset"


def test_endpoint_probe_sends_when_notify_on(monkeypatch):
    monkeypatch.setenv("MOLTRUST_NOTIFY", "on")
    monkeypatch.setattr(EP, "DRY_RUN", False)
    monkeypatch.setattr(EP.notify, "chat_id_for", lambda ch: "test-chat")
    calls = []
    monkeypatch.setattr(notify.requests, "post", _recorder(calls))

    EP.send_telegram("hi")
    assert len(calls) == 1, "endpoint_probe must POST exactly once when notify enabled"
    assert "api.telegram.org" in calls[0][0]
