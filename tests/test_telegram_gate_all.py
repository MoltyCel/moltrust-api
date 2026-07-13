"""The shared Telegram production-gate (app/notify.py) plus a representative
subset of the migrated senders.

Proves:
  * telegram_allowed() is False unless MOLTRUST_ENV == production.
  * notify.send_telegram() never touches requests.post outside production, and
    does exactly once inside production (with secrets present).
  * A representative subset of the real senders route their send path through
    the gate — no network call fires when MOLTRUST_ENV != production.

Runnable with no network / no DB: every network primitive is monkeypatched to a
recorder, and senders whose heavy deps are absent are skipped, not failed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import notify


# ── The shared gate ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", [None, "", "test", "staging", "development", "PRODUCTIONX"])
def test_telegram_allowed_false_outside_production(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("MOLTRUST_ENV", raising=False)
    else:
        monkeypatch.setenv("MOLTRUST_ENV", env)
    assert notify.telegram_allowed("ctx") is False


@pytest.mark.parametrize("env", ["production", "Production", "PRODUCTION"])
def test_telegram_allowed_true_in_production(monkeypatch, env):
    monkeypatch.setenv("MOLTRUST_ENV", env)
    assert notify.telegram_allowed("ctx") is True


# ── The shared sender ────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        class _Resp:
            status_code = 200
        return _Resp()


def test_send_telegram_no_post_outside_production(monkeypatch):
    monkeypatch.delenv("MOLTRUST_ENV", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    rec = _Recorder()
    monkeypatch.setattr(notify.requests, "post", rec)

    assert notify.send_telegram("hello") is False
    assert rec.calls == []


def test_send_telegram_posts_in_production(monkeypatch):
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    rec = _Recorder()
    monkeypatch.setattr(notify.requests, "post", rec)

    assert notify.send_telegram("hello", parse_mode="HTML") is True
    assert len(rec.calls) == 1
    url, kwargs = rec.calls[0]
    assert "api.telegram.org" in url and "/sendMessage" in url
    assert kwargs["json"]["chat_id"] == "chat"
    assert kwargs["json"]["parse_mode"] == "HTML"


def test_send_telegram_missing_secrets_noops_in_production(monkeypatch):
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    rec = _Recorder()
    monkeypatch.setattr(notify.requests, "post", rec)

    assert notify.send_telegram("hello") is False
    assert rec.calls == []


def test_send_telegram_chunks_long_text_in_production(monkeypatch):
    monkeypatch.setenv("MOLTRUST_ENV", "production")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    rec = _Recorder()
    monkeypatch.setattr(notify.requests, "post", rec)

    long_text = "\n".join("x" * 500 for _ in range(20))  # ~10k chars
    assert notify.send_telegram(long_text, chunk=True) is True
    assert len(rec.calls) >= 2  # split into multiple 3900-char chunks
    for _url, kwargs in rec.calls:
        assert len(kwargs["json"]["text"]) <= 3900


# ── Representative subset of migrated real senders ───────────────────────────
# Each is import-guarded: a sender whose heavy deps are unavailable is skipped,
# never failed. We prove the gate short-circuits the send outside production.

def _import(modpath):
    import importlib
    try:
        return importlib.import_module(modpath)
    except Exception as e:  # missing heavy dep (tweepy/web3/psycopg2/...) -> skip
        pytest.skip(f"cannot import {modpath}: {e}")


def _patch_no_network(monkeypatch):
    """Patch both httpx.post and requests.post to a shared recorder."""
    rec = _Recorder()
    for lib in ("httpx", "requests"):
        try:
            mod = __import__(lib)
        except Exception:
            continue
        monkeypatch.setattr(mod, "post", rec)
    return rec


@pytest.mark.parametrize("modpath,func,noop", [
    ("agents.news_scout", "send_telegram", False),
    ("agents.pr_monitor", "send_telegram", False),
    ("agents.watchdog",   "send_telegram", False),
    ("scripts.endpoint_probe", "send_telegram", None),
])
def test_real_senders_gated_outside_production(monkeypatch, modpath, func, noop):
    monkeypatch.delenv("MOLTRUST_ENV", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    mod = _import(modpath)
    rec = _patch_no_network(monkeypatch)

    result = getattr(mod, func)("gate-test message")
    assert result == noop, f"{modpath}.{func} should return its no-op value"
    assert rec.calls == [], f"{modpath}.{func} must not send outside production"
