"""scripts/a2a_role_guard.py judges both directions; each has a red and a green case."""
from scripts import a2a_role_guard as g

_REFUSED = {"jsonrpc": "2.0", "id": 1, "error": {
    "code": -32004, "message": "This agent does not execute A2A tasks. Its skills run over the MolTrust REST API"}}
# What /a2a answered until 21.10.2026.
_OLD = {"jsonrpc": "2.0", "id": 1, "result": {"message": {"role": "ROLE_AGENT", "parts": [{"text": "MolTrust Trust Registry"}]}}}


def test_a_refusal_with_rest_is_green():
    assert g.judge_task(_REFUSED) is None


def test_the_old_success_answer_is_red():
    assert "result" in g.judge_task(_OLD)


def test_another_error_code_is_red():
    assert "-32601" in g.judge_task({"error": {"code": -32601, "message": "Method not found"}})


def test_a_refusal_without_rest_is_red():
    assert "REST" in g.judge_task({"error": {"code": -32004, "message": "This operation is not supported"}})


def test_an_unsigned_card_is_red():
    assert g.judge_card(b'{"description": "executes no tasks"}', {"kty": "OKP", "crv": "Ed25519", "x": "AA"})


def test_a_card_that_is_not_json_is_red():
    assert "JSON" in g.judge_card(b"<html>", {})
