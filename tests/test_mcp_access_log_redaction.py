"""The MCP HTTP service writes no query string, and so no api_key, to its access log.

Drives uvicorn's own logging config and access-log format, so the test fails if
uvicorn's config ever starts dropping logger filters. Needs uvicorn, no
database, no network. The key is made up.
"""
import io
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "services"))

import access_log_redaction  # noqa: E402

FAKE_KEY = "mt_" + "f00d" * 8


def _access_line(path):
    import uvicorn

    access_log_redaction.install()
    uvicorn.Config(app=lambda *a: None, log_level="info")  # applies uvicorn's dictConfig
    logger = logging.getLogger("uvicorn.access")
    buf = io.StringIO()
    handler = logger.handlers[0]
    handler.stream = buf
    logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "POST", path, "1.1", 200)
    return buf.getvalue()


def test_the_key_does_not_reach_the_log():
    line = _access_line(f"/mcp?api_key={FAKE_KEY}")
    assert FAKE_KEY not in line
    assert "api_key" not in line
    assert "/mcp?[query removed]" in line


def test_a_path_without_query_is_left_as_it_is():
    assert '"POST /mcp HTTP/1.1" 200' in _access_line("/mcp")


def test_the_filter_survives_uvicorn_reconfiguring_logging():
    import uvicorn

    access_log_redaction.install()
    uvicorn.Config(app=lambda *a: None, log_level="info")
    uvicorn.Config(app=lambda *a: None, log_level="info")
    filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(f, access_log_redaction.DropQueryString) for f in filters) == 1


def test_mcp_http_installs_the_filter_before_it_runs():
    src = (pathlib.Path(__file__).resolve().parents[1] / "services" / "mcp_http.py").read_text()
    main = src[src.index('if __name__ == "__main__":'):]
    assert main.index("_drop_query_strings()") < main.index("mcp.run(")
