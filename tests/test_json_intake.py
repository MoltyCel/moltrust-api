"""Strict JSON intake on /vc/aae/evaluate and /vc/aae/submit (ops JI).

Before JI both endpoints read the body with request.json(): a repeated member
name kept its last value, and a signed request whose Content-Digest covered
"domain": "evil.example.com" was evaluated on the "domain" that followed it.
These cases hold the parser and show, through the real app, that the refusal
comes before authentication and names its own reason.
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.enforcement import json_intake as J

REF = "sha256:" + "0" * 64


def _nested(levels: int) -> bytes:
    """A body whose deepest point has `levels` arrays/objects open, the top object included."""
    inner = "[" * (levels - 1) + "]" * (levels - 1)
    return ('{"aae_ref": "%s", "x": %s}' % (REF, inner or "0")).encode()


# --- the parser ---

def test_valid_body_parses():
    assert J.parse(b'{"aae_ref": "a", "action_context": {"nonce": "n"}}') == {
        "aae_ref": "a", "action_context": {"nonce": "n"}}


@pytest.mark.parametrize("raw", [
    b'{"a": 1, "a": 2}',
    b'{"action_context": {"domain": "evil.example.com", "domain": "api.moltrust.ch"}}',
    b'{"x": [{"y": {"z": 1, "z": 1}}]}',  # same value twice is still two members
])
def test_duplicate_member_refused_at_any_depth(raw):
    with pytest.raises(J.JsonIntakeError) as e:
        J.parse(raw)
    assert e.value.code == "duplicate-member"


def test_depth_at_the_limit_parses_and_one_over_is_refused():
    assert J.nesting_depth(_nested(J.MAX_INTAKE_DEPTH)) == J.MAX_INTAKE_DEPTH
    J.parse(_nested(J.MAX_INTAKE_DEPTH))
    with pytest.raises(J.JsonIntakeError) as e:
        J.parse(_nested(J.MAX_INTAKE_DEPTH + 1))
    assert e.value.code == "nesting-too-deep"
    assert f"MAX_INTAKE_DEPTH={J.MAX_INTAKE_DEPTH}" in str(e.value)


def test_very_deep_body_is_refused_before_the_parser_recurses():
    raw = b'{"x": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    with pytest.raises(J.JsonIntakeError) as e:
        J.parse(raw)
    assert e.value.code == "nesting-too-deep"


def test_brackets_inside_strings_do_not_count():
    raw = json.dumps({"s": "[[[[{{{{" * 20, "q": 'a\\"[[[['}).encode()
    assert J.nesting_depth(raw) == 1
    J.parse(raw)


@pytest.mark.parametrize("raw,code", [
    (b"\xff\xfe", "not-json"),
    (b'{"a": ', "not-json"),
    (b"[1, 2]", "not-an-object"),
])
def test_other_refusals_name_their_reason(raw, code):
    with pytest.raises(J.JsonIntakeError) as e:
        J.parse(raw)
    assert e.value.code == code


# --- through the app: intake comes before authentication ---

def _post(path: str, raw: bytes):
    # No `with`: the lifespan opens the database pool; intake and auth refuse first.
    return TestClient(main.app).post(path, content=raw, headers={"Content-Type": "application/json"})


@pytest.mark.parametrize("path", ["/vc/aae/evaluate", "/vc/aae/submit"])
def test_duplicate_member_refused_before_auth(path):
    r = _post(path, b'{"aae_ref": "x", "aae_ref": "%s"}' % REF.encode())
    assert r.status_code == 422, r.text
    assert "duplicate-member" in r.json()["detail"]


@pytest.mark.parametrize("path", ["/vc/aae/evaluate", "/vc/aae/submit"])
def test_too_deep_refused_before_auth(path):
    r = _post(path, _nested(J.MAX_INTAKE_DEPTH + 1))
    assert r.status_code == 422, r.text
    assert "nesting-too-deep" in r.json()["detail"]


@pytest.mark.parametrize("path", ["/vc/aae/evaluate", "/vc/aae/submit"])
def test_valid_body_passes_intake_and_reaches_auth(path):
    r = _post(path, _nested(J.MAX_INTAKE_DEPTH))
    assert r.status_code == 401, r.text


# --- the live guard, judged here against the app instead of production ---

def _guard():
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import json_intake_guard
    return json_intake_guard


def _send_to_app(url: str, body: bytes):
    r = _post(url.replace("https://guard.test", ""), body)
    detail = r.json().get("detail")
    return r.status_code, detail if isinstance(detail, str) else ""


def test_guard_is_green_against_the_app():
    G = _guard()
    ok, lines = G.run("https://guard.test", send=_send_to_app)
    assert ok, lines
    assert lines[-1] == "json-intake=ok"


def test_guard_fault_probe_is_red():
    G = _guard()
    ok, lines = G.run("https://guard.test", send=_send_to_app, duplicate=G.VALID)
    assert not ok
    assert lines[-1].startswith("json-intake=FAIL /vc/aae/evaluate richtung1")


def test_guard_names_last_wins_and_closed_intake():
    G = _guard()
    last_wins = {p: {"duplicate": (401, "Authentication required"), "valid": (401, "x")}
                 for p in G.PATHS}
    ok, lines = G.judge(last_wins)
    assert not ok and "richtung1" in lines[-1] and "richtung2" not in lines[-1]
    closed = {p: {"duplicate": (422, "JSON intake refused the body: duplicate-member: x"),
                  "valid": (422, "JSON intake refused the body: not-json: x")} for p in G.PATHS}
    ok, lines = G.judge(closed)
    assert not ok and "richtung2" in lines[-1] and "richtung1" not in lines[-1]
