"""Every 422 on /identity/register-pop names its reason in the journal.

Before app/main.py grew a handler for ``RequestValidationError``, FastAPI's body
validation answered 422 before the endpoint ran and nothing of ours logged it.
The journal held method, path and status and no reason, which is why the 18
refusals of 21.10.2026 could not be told apart. Three of the five addresses had
seen the 402 with the invitation first, so they were agents taking the invited
way, not crawlers.

The response itself must not change: callers already read the body FastAPI
builds, so the handler logs and then delegates.
"""
import asyncio
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.main import _VALIDATION_BODY_BYTES, app, validation_error_handler

PATH = "/identity/register-pop"
# TestClient without the context manager: no startup event, no database. A body
# that fails validation is refused before any endpoint code could need one.
client = TestClient(app)


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "moltrust"]


def _excerpt(line):
    marker = f"body[:{_VALIDATION_BODY_BYTES}]="
    assert marker in line, line
    return line.split(marker, 1)[1]


def test_422_on_register_pop_writes_reason_and_body_excerpt(caplog):
    with caplog.at_level(logging.INFO, logger="moltrust"):
        resp = client.post(PATH, json={"public_key": "not-a-key"})

    assert resp.status_code == 422
    # The shape FastAPI's own handler builds, unchanged by this handler.
    assert set(resp.json()) == {"detail"}

    lines = [l for l in _lines(caplog) if l.startswith(f"422 on POST {PATH}")]
    assert len(lines) == 1, _lines(caplog)
    assert "reason=" in lines[0]
    assert "missing" in lines[0]  # the fields left out, by type and not by value
    assert "public_key" in _excerpt(lines[0])


def test_body_excerpt_stops_at_200_bytes(caplog):
    long_name = "x" * 1000
    with caplog.at_level(logging.INFO, logger="moltrust"):
        resp = client.post(PATH, json={"display_name": long_name})

    assert resp.status_code == 422
    line = [l for l in _lines(caplog) if l.startswith("422 on POST")][0]
    excerpt = _excerpt(line)
    assert len(excerpt.encode("utf-8")) <= _VALIDATION_BODY_BYTES
    assert long_name not in line


def test_a_secret_shape_in_the_body_is_redacted(caplog):
    secret = "sk-ant-api" + "0" * 30
    with caplog.at_level(logging.INFO, logger="moltrust"):
        resp = client.post(PATH, json={"display_name": secret})

    assert resp.status_code == 422
    line = [l for l in _lines(caplog) if l.startswith("422 on POST")][0]
    assert secret not in line
    assert "[REDACTED]" in line


def _handle(path, body):
    """Call the handler on a path of our choosing, without a route for it."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "scheme": "https",
        "server": ("api.moltrust.ch", 443),
    }
    request = Request(scope)
    request._body = body
    exc = RequestValidationError(
        [{"loc": ("body", "public_key"), "type": "missing", "msg": "Field required"}]
    )
    return asyncio.run(validation_error_handler(request, exc))


def test_another_path_logs_the_reason_and_no_body(caplog):
    # The silence probe for the test above: a body excerpt is written for the
    # listed paths only, because elsewhere the body may hold a secret.
    with caplog.at_level(logging.INFO, logger="moltrust"):
        resp = _handle("/auth/signup", b'{"password": "hunter2"}')

    assert resp.status_code == 422
    line = [l for l in _lines(caplog) if l.startswith("422 on POST /auth/signup")][0]
    assert "reason=public_key:missing" in line
    assert "hunter2" not in line
    assert "body[:" not in line


def test_the_listed_path_keeps_its_excerpt_through_the_handler(caplog):
    with caplog.at_level(logging.INFO, logger="moltrust"):
        resp = _handle(PATH, b'{"challenge": "abc"}')

    assert resp.status_code == 422
    line = [l for l in _lines(caplog) if l.startswith(f"422 on POST {PATH}")][0]
    assert _excerpt(line) == '{"challenge": "abc"}'
