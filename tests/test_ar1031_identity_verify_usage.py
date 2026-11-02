"""/identity/verify without a usable DID (ops AR-1031).

Every such answer carries the same body: error did_required, usage, example and
the documentation link. Other methods get 405 with Allow: GET and the same
usage. /identity/verify/<valid DID> stays as it was.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main

USAGE = "GET /identity/verify/{did}"


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool; the no-DID answers need none.
    return TestClient(main.app)


def _assert_usage(r, status=400, error="did_required"):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["error"] == error
    assert body["usage"] == USAGE
    assert body["example"].startswith("/identity/verify/did:moltrust:")
    assert body["documentation"] == "https://api.moltrust.ch/docs"
    return body


@pytest.mark.parametrize("path", ["/identity/verify", "/identity/verify/"])
def test_bare_path_says_where_the_did_goes(path):
    # Both registered: without the second, Starlette answers 307 to the first.
    r = _client().get(path, follow_redirects=False)
    _assert_usage(r)
    assert "path" in r.json()["detail"]


def test_a_did_query_parameter_is_not_read():
    # 15 days of log: no caller sent the DID as ?did= (one SPIFFE ID, no DID).
    r = _client().get("/identity/verify?did=did:moltrust:d34ed796a4dc4698")
    _assert_usage(r)


@pytest.mark.parametrize("raw", [
    "%7Bdid%7D",   # the literal template, 485 times in the log
    "{did}", "<did>", ":did", "did:", "did:moltrust:", "d34ed796a4dc4698",
    "did:moltrust:%E2%80%A6",  # the example copied verbatim
])
def test_every_placeholder_gets_the_same_body(raw):
    body = _assert_usage(_client().get(f"/identity/verify/{raw}"))
    # The format help stays, for a caller who has a DID and mistyped it.
    assert body["detail"] == main.DID_FORM_HELP


@pytest.mark.parametrize("method", ["POST", "HEAD"])
@pytest.mark.parametrize("path", [
    "/identity/verify", "/identity/verify/", "/identity/verify/%7Bdid%7D",
    "/identity/verify/did:moltrust:d34ed796a4dc4698",
])
def test_other_methods_are_405_with_allow_and_usage(method, path):
    r = _client().request(method, path, follow_redirects=False)
    assert r.status_code == 405
    assert r.headers["allow"] == "GET"
    if method == "POST":
        _assert_usage(r, status=405, error="method_not_allowed")
    else:
        assert r.content == b""


def test_openapi_names_the_path_form_with_an_example():
    op = main.app.openapi()["paths"]["/identity/verify/{did}"]["get"]
    assert "/identity/verify/did:moltrust:" in op["description"]
    (param,) = op["parameters"]
    assert param["schema"]["examples"] == ["did:moltrust:d34ed796a4dc4698"]
    assert "did_required" in op["description"]
    # The no-DID helpers are not separate operations.
    assert "/identity/verify" not in main.app.openapi()["paths"]


# Schweigeprobe: a valid DID is answered by the unchanged verification logic.

def test_foreign_did_still_withheld_200():
    r = _client().get("/identity/verify/did:web:example.com")
    assert r.status_code == 200
    body = r.json()
    assert body["withheld"] is True and body["verified"] is False
    assert "error" not in body


async def test_own_did_still_verified_200(async_client, credit_test_agent):
    did, _ = await credit_test_agent(balance=100)
    r = await async_client.get(f"/identity/verify/{did}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["did"] == did and body["verified"] is True
    assert "error" not in body and "usage" not in body
