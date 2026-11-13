"""Auto-repair list of 07.11.2026 (ops AR-1107): the legacy agent.json under
/a2a, /vc/resolve, /auth/moltbook, /discovery/resources, /admin/index.html."""
from unittest import mock

from fastapi.testclient import TestClient

import app.main as main
import scripts.auto_repair as ar

CARD = {"name": "MolTrust Trust Registry", "url": "https://api.moltrust.ch/a2a",
        "skills": [{"id": "verify", "name": "Verify"}]}


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool; none of these routes needs it.
    return TestClient(main.app)


class TestA2aAgentJson:
    def test_same_answer_as_the_root_path(self):
        with mock.patch.object(main, "_load_public_agent_card", return_value=CARD):
            c = _client()
            root, a2a = c.get("/.well-known/agent.json"), c.get("/a2a/.well-known/agent.json")
        assert root.status_code == a2a.status_code == 200
        assert a2a.content == root.content
        assert a2a.headers["content-type"] == root.headers["content-type"]
        assert a2a.headers["cache-control"] == root.headers["cache-control"]
        assert a2a.json() == CARD

    def test_head_answers_200_on_both(self):
        with mock.patch.object(main, "_load_public_agent_card", return_value=CARD):
            c = _client()
            for p in ("/.well-known/agent.json", "/a2a/.well-known/agent.json"):
                r = c.head(p)
                assert r.status_code == 200, p
                assert r.content == b"", p

    def test_not_the_agent_card(self):
        # The /a2a alias of agent-card.json serves the signed file verbatim;
        # agent.json is the re-encoded card. Two documents, two handlers.
        assert "/a2a/.well-known/agent.json" not in main._VERBATIM_PATHS


class TestVcResolve:
    def test_did_resolution_not_found(self):
        r = _client().get("/vc/resolve/did:moltrust:vcone")
        assert r.status_code == 404
        body = r.json()
        assert body["didDocument"] is None
        assert body["didDocumentMetadata"] == {}
        err = body["didResolutionMetadata"]["error"]
        assert err["type"] == "https://www.w3.org/ns/did#NOT_FOUND"
        assert "/identity/resolve/" in err["detail"]

    def test_any_did_and_head(self):
        c = _client()
        assert c.get("/vc/resolve/did:moltrust:0123456789abcdef").status_code == 404
        assert c.head("/vc/resolve/did:moltrust:vcone").status_code == 404

    def test_nothing_is_looked_up(self):
        # No pool needed and none touched: the answer does not depend on the DID.
        with mock.patch.object(main, "db_pool", None):
            a = _client().get("/vc/resolve/did:moltrust:vcone").json()
            b = _client().get("/vc/resolve/did:moltrust:0123456789abcdef").json()
        assert a == b


class TestExpected404:
    def test_the_four_are_expected(self):
        for p in ("/vc/resolve/did:moltrust:vcone", "/auth/moltbook",
                  "/discovery/resources", "/admin/index.html"):
            assert ar.expected_reason(p), p

    def test_neighbours_are_not(self):
        for p in ("/vc/resolve", "/auth/moltbook/x", "/auth", "/discovery",
                  "/discovery/resources/x", "/admin/index.htm", "/admin/indexXhtml",
                  "/admin/me", "/a2a/.well-known/agent.json"):
            assert ar.expected_reason(p) is None, p
