"""/.well-known/api-catalog (RFC 9727) and the /api index, from the auto_repair
digest of 22.10.2026 (ops AR-1022). Both answered 404 before."""
from fastapi.testclient import TestClient

import app.main as main

PROFILE = "https://www.rfc-editor.org/info/rfc9727"


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool, and neither route
    # touches it.
    return TestClient(main.app)


def test_routes_registered():
    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/.well-known/api-catalog" in paths
    assert "/api" in paths


def test_api_catalog_is_a_linkset_with_the_rfc_9727_profile():
    r = _client().get("/.well-known/api-catalog")
    assert r.status_code == 200
    ctype = r.headers["content-type"]
    assert ctype.startswith("application/linkset+json")
    assert f'profile="{PROFILE}"' in ctype


def test_api_catalog_links_openapi_docs_and_status():
    linkset = _client().get("/.well-known/api-catalog").json()["linkset"]
    assert len(linkset) == 1
    ctx = linkset[0]
    assert ctx["anchor"] == "https://api.moltrust.ch"
    assert ctx["service-desc"][0]["href"] == "https://api.moltrust.ch/openapi.json"
    assert ctx["service-doc"][0]["href"] == "https://api.moltrust.ch/docs"
    assert ctx["status"][0]["href"] == "https://api.moltrust.ch/health"
    assert ctx["service-meta"][0]["href"] == "https://api.moltrust.ch/.well-known/ard.json"
    # RFC 9264 section 4.2: every target is an array of objects with href.
    for rel, targets in ctx.items():
        if rel == "anchor":
            continue
        assert isinstance(targets, list) and all("href" in t for t in targets), rel


def test_head_on_the_catalog_carries_the_api_catalog_link():
    # RFC 9727 section 2: a HEAD request is answered with a Link header whose
    # relation is api-catalog.
    r = _client().head("/.well-known/api-catalog")
    assert r.status_code == 200
    assert r.headers["link"] == '</.well-known/api-catalog>; rel="api-catalog"'
    assert r.content == b""


def test_api_index_names_the_api_and_links_the_catalog():
    r = _client().get("/api")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["name"] == "MolTrust API"
    assert body["version"] == main.API_VERSION
    assert body["links"] == {
        "openapi": "https://api.moltrust.ch/openapi.json",
        "docs": "https://api.moltrust.ch/docs",
        "api-catalog": "https://api.moltrust.ch/.well-known/api-catalog",
    }
    assert 'rel="api-catalog"' in r.headers["link"]


def test_the_index_does_not_open_the_rest_of_api():
    # /api is an index, not a prefix. The two other paths from the same digest
    # stay unanswered on purpose (exception list, scripts/auto_repair.py).
    c = _client()
    for p in ("/api/credentials", "/api/did/resolve"):
        assert c.get(p).status_code == 404, p
