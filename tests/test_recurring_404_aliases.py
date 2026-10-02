"""Routes added for the recurring 404s in the auto_repair digest (Oct 2026):
/a2a/health, /.well-known/security.txt, /.well-known/mcp/server-card.json,
/api/credential/issue."""
import asyncio
import json
from unittest import mock

from starlette.requests import Request

import app.main as main


def _req(query: str = "") -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/credential/issue",
                    "query_string": query.encode(), "headers": []})


def test_routes_registered():
    paths = {getattr(r, "path", None) for r in main.app.routes}
    for p in ("/a2a/health", "/.well-known/security.txt",
              "/.well-known/mcp/server-card.json", "/api/credential/issue"):
        assert p in paths, p


def test_a2a_health_status_and_version():
    resp = asyncio.run(main.a2a_health())
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"status": "ok", "version": main.API_VERSION}


def test_a2a_health_not_logged():
    assert "/a2a/health" in main.SKIP_LOG_PATHS


def test_security_txt_served_as_text(tmp_path):
    (tmp_path / "security.txt").write_text("Contact: mailto:hello@moltrust.ch\n")
    with mock.patch.object(main, "_WEB_ROOT_WELL_KNOWN", str(tmp_path)):
        resp = asyncio.run(main.well_known_security_txt())
    assert resp.status_code == 200
    assert resp.body.decode().startswith("Contact: mailto:")
    assert resp.headers["content-type"].startswith("text/plain")


def test_security_txt_missing_is_404(tmp_path):
    with mock.patch.object(main, "_WEB_ROOT_WELL_KNOWN", str(tmp_path)):
        try:
            asyncio.run(main.well_known_security_txt())
        except main.HTTPException as e:
            assert e.status_code == 404
        else:
            raise AssertionError("expected 404")


def test_server_card_served(tmp_path):
    (tmp_path / "mcp").mkdir()
    card = {"serverInfo": {"name": "x"}, "tools": "dynamic"}
    (tmp_path / "mcp" / "server-card.json").write_text(json.dumps(card))
    with mock.patch.object(main, "_WEB_ROOT_WELL_KNOWN", str(tmp_path)):
        resp = asyncio.run(main.well_known_mcp_server_card())
    assert resp.status_code == 200
    assert json.loads(resp.body) == card


def test_credential_issue_redirects_308_to_moltguard():
    resp = asyncio.run(main.moltguard_credential_issue_alias(_req()))
    assert resp.status_code == 308
    assert resp.headers["location"] == "/guard/api/credential/issue"
    resp = asyncio.run(main.moltguard_credential_issue_alias(_req("a=1")))
    assert resp.headers["location"] == "/guard/api/credential/issue?a=1"


def test_transparency_redirects_301_to_web_page():
    assert "/transparency" in {getattr(r, "path", None) for r in main.app.routes}
    resp = asyncio.run(main.transparency_redirect())
    assert resp.status_code == 301
    assert resp.headers["location"] == "https://moltrust.ch/transparency.html"
