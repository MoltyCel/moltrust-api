"""Bucket and limit for POST /enforce/check: per facilitator key, else per client IP.

The limiter keys every route by client IP, so a facilitator running the enforce check as a
counter-check on every payment shares one 60/minute bucket with everything behind its
egress IP. Keys with api_keys.tier = 'facilitator' get their own bucket. Measured against
a bare FastAPI app with the same decorator form as the route; the route itself is checked
in its source. Draft from Tab 2 (2026-10-11), first tested there under slowapi 0.1.9.
"""
import asyncio
import pathlib

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app import enforce_ratelimit as rl

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _ip_key(request):
    return request.client.host if request.client else "unknown"


def _client():
    limiter = Limiter(key_func=_ip_key)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.post("/enforce/check")
    @limiter.limit(rl.enforce_limit, key_func=rl.make_rate_key(_ip_key))
    async def check(request: Request):
        return {"ok": True}

    return TestClient(app)


def _ok(client, n, headers=None):
    return sum(client.post("/enforce/check", headers=headers or {}).status_code == 200
               for _ in range(n))


def test_ip_bucket_stays_at_60(monkeypatch):
    monkeypatch.setattr(rl, "_facilitator_hashes", frozenset())
    assert _ok(_client(), 65) == 60


def test_unknown_key_falls_back_to_the_ip_bucket(monkeypatch):
    monkeypatch.setattr(rl, "_facilitator_hashes", frozenset({rl._hash("fac-key")}))
    assert _ok(_client(), 65, {"X-API-Key": "random"}) == 60


def test_facilitator_key_gets_its_own_bucket_and_limit(monkeypatch):
    monkeypatch.setattr(rl, "_facilitator_hashes", frozenset({rl._hash("fac-key")}))
    c = _client()
    assert _ok(c, 60) == 60                                  # IP bucket used up
    assert _ok(c, 605, {"X-API-Key": "fac-key"}) == 600      # own bucket, 600/minute


def test_refresh_holds_hashes_only(monkeypatch):
    class _Conn:
        async def fetch(self, sql):
            assert "tier = 'facilitator'" in sql and "active = true" in sql
            return [{"key": "k1"}, {"key": "k2"}]

    class _Pool:
        def acquire(self):
            pool = self

            class _Ctx:
                async def __aenter__(self_inner):
                    return _Conn()

                async def __aexit__(self_inner, *a):
                    return False
            return _Ctx()

    monkeypatch.setattr(rl, "_facilitator_hashes", frozenset())
    assert asyncio.run(rl.refresh(_Pool())) == 2
    assert rl._facilitator_hashes == frozenset({rl._hash("k1"), rl._hash("k2")})
    assert "k1" not in rl._facilitator_hashes


def test_the_route_uses_the_enforce_bucket():
    src = (ROOT / "app" / "main.py").read_text()
    i = src.index('@app.post("/enforce/check"')
    assert src[i:i + 200].count("@limiter.limit(_enforce_limit, key_func=_enforce_rate_key)") == 1
