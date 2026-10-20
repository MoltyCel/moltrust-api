"""/molcheck/* serves MolCheck, /proof/* stays as a deprecated alias (ops MC-API).

Both prefixes must forward to the same upstream path with the same method, query,
body and headers, and hand back status, body and headers unchanged — a 402 from
the paywall included. The upstream is replaced by an httpx MockTransport; nothing
here leaves the process.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import molcheck

AGENT = "did:moltrust:0123456789abcdef"
REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture()
def upstream(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/proof/evidence/"):
            return httpx.Response(402, json={"error": "payment_required", "price": "0.05"},
                                  headers={"x-payment-required": "1"})
        return httpx.Response(200, json={"path": request.url.path},
                              headers={"x-molcheck-test": "yes"})

    def client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return REAL_ASYNC_CLIENT(*a, **k)

    monkeypatch.setattr(molcheck.httpx, "AsyncClient", client)
    return seen


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool, and these routes never touch it.
    return TestClient(main.app)


GETS = [f"/verdict-free/{AGENT}", f"/verdict/{AGENT}", f"/mandate/{AGENT}",
        "/registry", "/info", "/health"]


@pytest.mark.parametrize("prefix", ["/molcheck", "/proof"])
@pytest.mark.parametrize("path", GETS)
def test_get_forwards_to_the_proof_path(upstream, prefix, path):
    r = _client().get(prefix + path, params={"a": "1"})
    assert r.status_code == 200
    assert r.json() == {"path": "/proof" + path}
    assert r.headers["x-molcheck-test"] == "yes"
    assert upstream[-1].method == "GET"
    assert upstream[-1].url.path == "/proof" + path
    assert upstream[-1].url.params["a"] == "1"


@pytest.mark.parametrize("prefix", ["/molcheck", "/proof"])
def test_verify_forwards_the_body_unchanged(upstream, prefix):
    body = b'{"agent":"0x1","mandate":{},"actions":[]}'
    r = _client().post(prefix + "/verify", content=body,
                       headers={"content-type": "application/json"})
    assert r.status_code == 200
    assert upstream[-1].method == "POST"
    assert upstream[-1].url.path == "/proof/verify"
    assert upstream[-1].content == body


@pytest.mark.parametrize("prefix", ["/molcheck", "/proof"])
def test_payment_required_passes_through(upstream, prefix):
    r = _client().get(f"{prefix}/evidence/{AGENT}", headers={"X-PAYMENT": "token-123"})
    assert r.status_code == 402
    assert r.json()["error"] == "payment_required"
    assert r.headers["x-payment-required"] == "1"
    assert upstream[-1].headers["x-payment"] == "token-123"


def test_the_client_address_travels_and_a_spoofed_one_does_not(upstream):
    # TestClient's peer is "testclient", not a trusted proxy: a caller-supplied
    # X-Real-IP must not reach the service, the resolved peer does.
    _client().get("/molcheck/registry", headers={"X-Real-IP": "6.6.6.6",
                                                 "X-Forwarded-For": "6.6.6.6"})
    sent = upstream[-1].headers
    assert sent["x-real-ip"] == "testclient"
    assert "6.6.6.6" not in sent.get("x-forwarded-for", "")


def test_an_agent_value_cannot_climb_out_of_proof(upstream):
    r = _client().get("/molcheck/verdict/..%2F..%2Fmcp")
    if not upstream:
        assert r.status_code == 404      # the route did not match at all
        return
    raw = upstream[-1].url.raw_path
    assert raw.startswith(b"/proof/verdict/")
    assert b"/../" not in raw and not raw.endswith(b"/mcp")


def test_upstream_down_is_a_502_not_a_500(monkeypatch):
    def client(*a, **k):
        def boom(request):
            raise httpx.ConnectError("refused", request=request)
        k["transport"] = httpx.MockTransport(boom)
        return REAL_ASYNC_CLIENT(*a, **k)

    monkeypatch.setattr(molcheck.httpx, "AsyncClient", client)
    r = _client().get("/molcheck/health")
    assert r.status_code == 502
    assert r.json() == {"error": "molcheck_unavailable"}


def test_openapi_lists_molcheck_and_marks_proof_deprecated():
    paths = main.app.openapi()["paths"]
    for _name, (method, path, _summary) in molcheck.ENDPOINTS.items():
        new = paths["/molcheck" + path][method.lower()]
        old = paths["/proof" + path][method.lower()]
        assert not new.get("deprecated")
        assert old["deprecated"] is True
        assert old["description"].startswith(f"Deprecated alias of `/molcheck{path}`")
