"""Tests — rate-limit key uses the CIDR-gated IP resolver (anti-spoofing).

server-only Hotfix war: _ratelimit_key vertraute X-Real-IP/X-Forwarded-For ungated
-> jeder externe Client konnte das Rate-Limit-Keying spoofen. Jetzt delegiert es an
_get_client_ip (nur ein vertrauenswuerdiger Upstream-Proxy darf die Header setzen).
"""
import pytest


class _Req:
    def __init__(self, peer, headers=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers or {}


def test_xff_spoof_ignored_from_untrusted_peer():
    from app.main import _ratelimit_key, _get_client_ip
    # externer (untrusted) Peer spooft X-Forwarded-For -> ignoriert, echter Peer als Key
    req = _Req("203.0.113.7", {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"})
    assert _get_client_ip(req) == "203.0.113.7"
    assert _ratelimit_key(req) == "203.0.113.7"  # NICHT 1.2.3.4 / 5.6.7.8


def test_xff_honoured_from_trusted_proxy(monkeypatch):
    monkeypatch.setenv("MOLTRUST_TRUSTED_PROXIES", "10.0.0.0/8")  # deterministisch
    from app.main import _ratelimit_key, _get_client_ip
    req = _Req("10.0.0.5", {"X-Forwarded-For": "1.2.3.4"})  # trusted upstream
    assert _get_client_ip(req) == "1.2.3.4"
    assert _ratelimit_key(req) == "1.2.3.4"


def test_no_headers_uses_peer():
    from app.main import _ratelimit_key
    assert _ratelimit_key(_Req("198.51.100.9")) == "198.51.100.9"
