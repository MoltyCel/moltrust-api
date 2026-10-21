"""/a2a declares capabilities and executes no tasks (ops job A2A, 21.10.2026).

Until then SendMessage answered every message with the same success text,
whatever it asked for: "hello", a trust-score question and an invented skill
all came back as a result. Now a task message is refused with
UnsupportedOperationError (-32004), the refusal names the REST way, a malformed
message still gets the field at fault (-32602), and the public card says that
each skill runs over REST at a named route."""
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import a2a_server

_V1 = {"A2A-Version": "1.0"}
_CARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     ".well-known", "agent-card.json")


def _message(text: str) -> dict:
    return {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": text}]}}


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    assert a2a_server.mount_a2a(app)
    return TestClient(app)


def _rpc(client, method, params, path=a2a_server.A2A_RPC_PATH):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = client.post(path, json=body, headers=_V1)
    assert r.status_code == 200
    return r.json()


@pytest.mark.parametrize("text", [
    "hello",
    "What is the trust score of did:moltrust:0000000000000000?",
    '{"skill": "launch-rockets"}',
])
@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
def test_a_valid_task_message_is_refused_with_the_reason(client, method, text):
    err = _rpc(client, method, _message(text))["error"]
    assert err["code"] == -32004
    assert err["message"] == a2a_server.TASKS_NOT_SUPPORTED
    info = err["data"][0]
    assert info["reason"] == "UNSUPPORTED_OPERATION"
    assert info["metadata"]["restApi"] == "https://api.moltrust.ch/openapi.json"
    assert info["metadata"]["agentCard"] == "https://api.moltrust.ch/.well-known/agent-card.json"


def test_no_task_message_gets_a_result(client):
    # The defect itself: a success answer to a task the agent does not execute.
    assert "result" not in _rpc(client, "SendMessage", _message("hello"))


def test_a_malformed_message_names_the_field_not_the_refusal(client):
    err = _rpc(client, "SendMessage", {"message": {"messageId": "m", "role": "ROLE_USER", "parts": []}})["error"]
    assert err["code"] == -32602


def test_the_refusal_points_to_rest():
    assert "REST" in a2a_server.TASKS_NOT_SUPPORTED
    assert "https://api.moltrust.ch/openapi.json" in a2a_server.TASKS_NOT_SUPPORTED


def test_reads_stay_with_the_sdk(client):
    assert _rpc(client, "GetTask", {"id": "unknown"})["error"]["code"] == -32001


def test_skills_are_read_from_the_published_card():
    card = json.load(open(_CARD, encoding="utf-8"))
    assert a2a_server._SKILLS == [(s["id"], s["name"]) for s in card["skills"]]
    assert a2a_server._SKILLS


def test_the_card_says_tasks_are_not_executed():
    card = json.load(open(_CARD, encoding="utf-8"))
    assert "Reference implementation" not in card["description"]
    assert "executes no tasks" in card["description"]
    assert "-32004" in card["description"]
    for s in card["skills"]:
        assert "not as an A2A task" in s["description"], s["id"]
