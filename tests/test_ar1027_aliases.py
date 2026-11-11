"""Auto-repair list of 27.10.2026 (ops AR-1027): the agent card under /a2a,
/api/v1, /.well-known/acp.json, and the MCP server's MoltGuard calls."""
import json
import pathlib

from fastapi.testclient import TestClient

import app.main as main
import scripts.auto_repair as ar

CARD = pathlib.Path(__file__).resolve().parents[1] / ".well-known" / "agent-card.json"


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool; none of these routes needs it.
    return TestClient(main.app)


class TestA2aAgentCard:
    def test_the_alias_serves_the_file_byte_for_byte(self, monkeypatch):
        # Through the whole middleware chain: re-encoded JSON would differ.
        monkeypatch.setattr(main, "PUBLIC_AGENT_CARD_FILE", str(CARD))
        r = _client().get("/a2a/.well-known/agent-card.json")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/json"
        assert r.content == CARD.read_bytes()

    def test_head_answers_without_a_body(self, monkeypatch):
        monkeypatch.setattr(main, "PUBLIC_AGENT_CARD_FILE", str(CARD))
        r = _client().head("/a2a/.well-known/agent-card.json")
        assert r.status_code == 200
        assert r.content == b""

    def test_it_reads_the_file_on_every_request(self, monkeypatch, tmp_path):
        # A web deploy replaces the card without restarting the API.
        f = tmp_path / "card.json"
        monkeypatch.setattr(main, "PUBLIC_AGENT_CARD_FILE", str(f))
        f.write_bytes(b'{"v": 1}\n')
        assert _client().get("/a2a/.well-known/agent-card.json").content == b'{"v": 1}\n'
        f.write_bytes(b'{"v": 2}\n')
        assert _client().get("/a2a/.well-known/agent-card.json").content == b'{"v": 2}\n'

    def test_the_alias_skips_the_content_filter(self):
        assert "/a2a/.well-known/agent-card.json" in main._VERBATIM_PATHS


class TestApiV1:
    def test_api_v1_is_the_api_index(self):
        c = _client()
        a, v1 = c.get("/api"), c.get("/api/v1")
        assert v1.status_code == 200
        assert v1.json() == a.json()
        assert v1.headers["link"] == a.headers["link"]

    def test_only_the_bare_prefix(self):
        assert _client().get("/api/v1/agents").status_code == 404


class TestAcpExpected:
    def test_acp_manifest_is_an_expected_404(self):
        reason = ar.expected_reason("/.well-known/acp.json")
        assert reason and "Agentic Commerce Protocol" in reason

    def test_its_neighbours_are_not(self):
        for p in ("/.well-known/acp", "/.well-known/acp.json/x", "/acp.json"):
            assert ar.expected_reason(p) is None, p


class TestMcpGuardRouting:
    SRC = (pathlib.Path(__file__).resolve().parents[1] / "services" / "mcp_http.py").read_text()

    def test_the_package_prefix_points_at_moltguard(self):
        # Read, not imported: importing mcp_http starts registering every tool.
        assert "_package.GUARD_PREFIX = MOLTGUARD_URL" in self.SRC
        set_at = self.SRC.index("_package.GUARD_PREFIX = MOLTGUARD_URL")
        assert set_at < self.SRC.index('if __name__ == "__main__":')

    def test_moltguard_url_is_the_local_port_nginx_uses(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "services"
               / "moltguard_mcp_tools.py").read_text()
        assert 'MOLTGUARD_URL = "http://127.0.0.1:3003"' in src

    def test_an_absolute_prefix_overrides_the_client_base(self):
        # The mechanism the fix relies on: httpx ignores base_url for an
        # absolute URL, so f"{GUARD_PREFIX}/skill/audit" leaves the API port.
        import httpx
        seen = []
        transport = httpx.MockTransport(lambda req: seen.append(str(req.url)) or httpx.Response(200, json={}))
        with httpx.Client(base_url="http://127.0.0.1:8000", transport=transport) as c:
            c.get("http://127.0.0.1:3003/skill/audit", params={"url": "https://github.com/a/b"})
        assert seen[0].startswith("http://127.0.0.1:3003/skill/audit?url=")


def test_card_file_in_repo_is_json():
    json.loads(CARD.read_bytes())
