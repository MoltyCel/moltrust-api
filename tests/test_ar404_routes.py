"""Routes for the recurring 404s in the auto_repair digest of 16.10.2026 (ops job
AR-404-1): /.well-known/ard.json and its predecessor ai-catalog.json, the A2A
paths /jsonrpc and /v1/message:send, and the /identity index the issuer DID
document points to."""
import asyncio
import json
import re

from fastapi.testclient import TestClient

import app.main as main
from app import a2a_server

# ard-entry.schema.json (ards-project/ard-spec 49eb4df), $defs/EntryFields.
_URN_AIR = re.compile(r"^urn:air:[a-zA-Z0-9.-]+(:[a-zA-Z0-9._-]+)+$")


def _ard_problems(doc: dict) -> list[str]:
    """What the ARD entry schema would reject in this manifest."""
    entries = doc.get("entries")
    if not isinstance(entries, list):
        return ["entries is not a list"]
    problems = []
    for i, e in enumerate(entries):
        for term in ("identifier", "displayName", "type"):
            if not isinstance(e.get(term), str):
                problems.append(f"entries[{i}] lacks {term}")
        if not _URN_AIR.match(str(e.get("identifier", ""))):
            problems.append(f"entries[{i}] identifier is not urn:air")
        if ("url" in e) == ("data" in e):
            problems.append(f"entries[{i}] needs exactly one of url/data")
    return problems


def _client() -> TestClient:
    # No `with`: the lifespan opens the database pool, and none of these
    # routes touches it.
    return TestClient(main.app)


def test_routes_registered():
    paths = {getattr(r, "path", None) for r in main.app.routes}
    for p in ("/.well-known/ard.json", "/.well-known/ai-catalog.json", "/identity",
              "/jsonrpc", "/v1/message:send"):
        assert p in paths, p


def test_ard_manifest_is_valid_and_names_card_and_mcp():
    doc = json.loads(asyncio.run(main.well_known_ard()).body)
    assert _ard_problems(doc) == []
    assert doc["version"] and doc["specVersion"] == "1.0"
    types = {e["type"] for e in doc["entries"]}
    assert {"application/a2a-agent-card+json", "application/mcp-server-card+json"} <= types


def test_ard_check_rejects_a_broken_entry():
    """The check above has to be able to fail."""
    broken = {"entries": [{"identifier": "moltrust", "displayName": "x",
                           "type": "application/json", "url": "u", "data": {}}]}
    problems = _ard_problems(broken)
    assert "entries[0] identifier is not urn:air" in problems
    assert "entries[0] needs exactly one of url/data" in problems
    assert _ard_problems({}) == ["entries is not a list"]


def test_ard_and_ai_catalog_serve_the_same_document():
    c = _client()
    a = c.get("/.well-known/ard.json")
    b = c.get("/.well-known/ai-catalog.json")
    assert a.status_code == b.status_code == 200
    assert a.headers["content-type"].startswith("application/json")
    assert a.json() == b.json()


def test_identity_index_lists_identity_paths_only():
    r = _client().get("/identity")
    assert r.status_code == 200
    endpoints = r.json()["endpoints"]
    paths = [e["path"] for e in endpoints]
    assert "/identity/resolve/{did}" in paths
    assert "/identity/register" in paths
    assert all(p.startswith("/identity/") for p in paths)
    assert all(e["methods"] for e in endpoints)


def test_identity_index_answers_head():
    assert _client().head("/identity").status_code == 200


_SEND = {"message": {"messageId": "ping-1", "role": "ROLE_USER", "parts": [{"text": "ping"}]}}
_V1 = {"A2A-Version": "1.0"}


def _agent_text(message: dict) -> str:
    return "".join(p.get("text", "") for p in message["parts"])


def test_jsonrpc_alias_answers_like_a2a():
    c = _client()
    body = {"jsonrpc": "2.0", "id": "ping-1", "method": "SendMessage", "params": _SEND}
    canonical = c.post(a2a_server.A2A_RPC_PATH, json=body, headers=_V1)
    alias = c.post("/jsonrpc", json=body, headers=_V1)
    assert canonical.status_code == alias.status_code == 200
    a, b = canonical.json(), alias.json()
    # messageId is a fresh uuid per answer; everything else must match.
    a["result"]["message"].pop("messageId")
    b["result"]["message"].pop("messageId")
    assert a == b
    assert _agent_text(b["result"]["message"]) == a2a_server._CAPABILITY_TEXT


def test_rest_message_send_answers_like_a2a():
    r = _client().post(a2a_server.A2A_REST_SEND_PATH, json=_SEND, headers=_V1)
    assert r.status_code == 200
    assert _agent_text(r.json()["message"]) == a2a_server._CAPABILITY_TEXT


def test_only_message_send_is_mounted_from_the_rest_binding():
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert not any(p.startswith("/v1/tasks") for p in paths)
    assert "/{tenant}" not in paths
